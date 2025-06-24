import dash
from dash import dcc, html, Input, Output, State, MATCH, ALL
import dash_bootstrap_components as dbc
from kiteconnect import KiteConnect
from kiteconnect import exceptions as kc_exceptions
import os
import webbrowser
import pandas as pd
import sqlite3
import plotly.graph_objects as go
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key, dotenv_values
import uuid
import time # Import time for potential delays

# Import the refactored kitews module for WebSocket functionality
import kitews

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
KITE_API_KEY = os.getenv("KITE_API_KEY", "YOUR_KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_KITE_API_SECRET")
KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:8050/login_response")
SAVED_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN") # Access token saved from previous session
INSTRUMENT_CSV_PATH = "kite_instruments.csv"
INSTRUMENT_DB_PATH = "instruments.db"

# Global variables for the Dash app
kite = None # KiteConnect REST API instance
access_token = SAVED_ACCESS_TOKEN # Current access token
user_profile = None # Stores user profile information
instrument_df = pd.DataFrame() # DataFrame to store instrument master data

# Define instrument tokens and strike steps for major indices
INDEX_INSTRUMENT_DETAILS = {
    "NIFTY": {"instrument_token": 256265, "strike_step": 50},
    "BANKNIFTY": {"instrument_token": 260105, "strike_step": 100},
    "FINNIFTY": {"instrument_token": 257801, "strike_step": 50},
    "MIDCPNIFTY": {"instrument_token": 288009, "strike_step": 25},
    "SENSEX": {"instrument_token": 265, "strike_step": 100},
    "BANKEX": {"instrument_token": 274441, "strike_step": 100}
}


# --- Automatic Token Validation and WebSocket Connection on Startup ---
if SAVED_ACCESS_TOKEN:
    try:
        # Attempt to initialize KiteConnect with the saved token
        temp_kite = KiteConnect(api_key=KITE_API_KEY)
        temp_kite.set_access_token(SAVED_ACCESS_TOKEN)
        profile_data = temp_kite.profile() # Validate token by fetching profile
        user_profile = profile_data.get("user_name")
        kite = temp_kite # Assign to global kite object if successful
        print("KiteConnect initialized with valid saved access token.")
        
        # Get all index tokens to subscribe permanently
        all_index_tokens_for_ws = [details["instrument_token"] for details in INDEX_INSTRUMENT_DETAILS.values()]
        kitews.start_websocket(KITE_API_KEY, SAVED_ACCESS_TOKEN, initial_tokens=all_index_tokens_for_ws)
    except (kc_exceptions.TokenException, kc_exceptions.PermissionException, Exception) as e:
        print(f"Saved access token is invalid or expired ({e}). Clearing token and stopping WebSocket.")
        access_token = None
        kite = None
        user_profile = None
        set_key('.env', "KITE_ACCESS_TOKEN", "") # Clear invalid token from .env
        kitews.stop_websocket() # Ensure WebSocket is stopped
        kitews.clear_live_quotes() # Clear any old live data
else:
    print("No saved access token found. User needs to authenticate.")

# --- Load Instrument Master from SQLite or CSV on Startup ---
def load_instruments():
    """
    Loads instrument master data from an SQLite database or a CSV file.
    Prioritizes SQLite for faster loading.
    """
    global instrument_df
    if os.path.exists(INSTRUMENT_DB_PATH):
        try:
            conn = sqlite3.connect(INSTRUMENT_DB_PATH)
            instrument_df = pd.read_sql('SELECT * FROM instruments', conn)
            conn.close()
            print(f"Instrument master loaded from {INSTRUMENT_DB_PATH}. Total instruments: {len(instrument_df)}")
        except Exception as e:
            print(f"Error loading from SQLite: {e}. Attempting to load from CSV.")
            # Fallback to CSV if SQLite fails
            if os.path.exists(INSTRUMENT_CSV_PATH):
                try:
                    instrument_df = pd.read_csv(INSTRUMENT_CSV_PATH)
                    print(f"Instrument master loaded from {INSTRUMENT_CSV_PATH}. Total instruments: {len(instrument_df)}")
                except Exception as csv_e:
                    print(f"Error loading from CSV: {csv_e}. Instrument data not loaded.")
                    instrument_df = pd.DataFrame() # Ensure instrument_df is empty on failure
    elif os.path.exists(INSTRUMENT_CSV_PATH):
        try:
            instrument_df = pd.read_csv(INSTRUMENT_CSV_PATH)
            print(f"Instrument master loaded from {INSTRUMENT_CSV_PATH}. Total instruments: {len(instrument_df)}")
        except Exception as e:
            print(f"Error loading from CSV: {e}. Instrument data not loaded.")
            instrument_df = pd.DataFrame() # Ensure instrument_df is empty on failure
    else:
        print("No instrument master file (DB or CSV) found. Please load instruments via the app.")

load_instruments()

# Initialize the Dash app
app = dash.Dash(__name__, external_stylesheets=[
    dbc.themes.FLATLY, # Using Flatly theme for a clean look
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css', # Font Awesome for icons
    '/assets/style.css' # Custom CSS for additional styling
])

# --- Dash Layout ---
# Determine initial state of authentication related buttons
is_authenticated_on_load = bool(access_token)

app.layout = dbc.Container([
    html.H1("Kite Connect by TRADECK", className="my-4 text-center"),
    dcc.Store(id="theme-store", data="light-theme"),  # Store for theme preference

    # dcc.Interval component to trigger periodic updates for live data
    dcc.Interval(
        id='interval-component',
        interval=2 * 1000, # interval in milliseconds (e.g., 2 seconds)
        n_intervals=0, # initial number of intervals
        disabled= not is_authenticated_on_load # Disable initially if not authenticated
    ),

    html.Div(
        dbc.Checklist(
            options=[{"label": "Dark Mode", "value": "dark"}],
            id="dark-mode-toggle",
            switch=True,
            className="mb-3"
        ),
        className="d-flex justify-content-end"
    ),
    dbc.Toast(
        id="notification-toast",
        header="Notification",
        is_open=False,
        dismissable=True,
        style={"position": "fixed", "top": 10, "right": 10, "width": 350, "zIndex": 1000}
    ),
    dbc.Accordion([
        dbc.AccordionItem([
            dbc.Row([
                dbc.Col(dbc.Card([
                    dbc.CardHeader("Kite Connect Setup"),
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col(html.Div([
                                dbc.Label("API Key:", className="mb-0"),
                                dbc.Input(id="api-key-input", type="text", placeholder="Enter your Kite API Key",
                                          value=KITE_API_KEY, debounce=True, required=True),
                            ]), md=12),
                            dbc.Col(html.Div([
                                dbc.Label("API Secret:", className="mb-0"),
                                dbc.Input(id="api-secret-input", type="text", placeholder="Enter your Kite API Secret",
                                          value=KITE_API_SECRET),
                            ]), md=12),
                        ], className="g-2 mb-3"),
                        html.Div([
                            dbc.Label("Redirect URL:", className="mb-0"),
                            dbc.Input(id="redirect-url-input", type="text", placeholder="e.g., http://127.0.0.1:8050/login_response",
                                      value=KITE_REDIRECT_URL, className="mb-3"),
                        ]),
                        dbc.Row([
                            dbc.Col(dbc.Button([html.I(className="fas fa-plug me-1"), "Initialize Kite & Get Login URL"],
                                               id="init-kite-button", color="primary", className="me-2",
                                               disabled=is_authenticated_on_load),
                                    width="auto"),
                            dbc.Col(dbc.Button([html.I(className="fas fa-sign-in-alt me-1"), "Open Kite Login Page"],
                                               id="open-login-button", color="info", className="ms-2",
                                               disabled=is_authenticated_on_load),
                                    width="auto"),
                            dbc.Col(dbc.Button([html.I(className="fas fa-trash me-1"), "Clear Saved Token"],
                                               id="clear-token-button", color="warning", className="ms-2",
                                               disabled=not is_authenticated_on_load),
                                    width="auto"),
                        ], className="g-2 justify-content-start"),
                        html.Div(id="login-url-output", className="mt-3"),
                    ], className="p-3")
                ], className="shadow-sm rounded-lg h-100"), md=6),
                dbc.Col(dbc.Card([
                    dbc.CardHeader("Authentication Response"),
                    dbc.CardBody([
                        html.Div([
                            dbc.Label("Request Token:", className="mb-0"),
                            dbc.Input(id="request-token-input", type="text", placeholder="Paste request_token here",
                                      className="mb-3"),
                        ]),
                        dbc.Button([html.I(className="fas fa-key me-1"), "Generate Access Token"],
                                   id="generate-token-button", color="success", disabled=is_authenticated_on_load),
                        html.Div(id="access-token-output", className="mt-3"),
                    ], className="p-3")
                ], className="shadow-sm rounded-lg h-100"), md=6),
            ], className="mb-4 g-3")
        ], title="Kite Connect Authentication", className="mb-4")
    ], start_collapsed=True), # Accordion starts collapsed by default


    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Trading Dashboard"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Button([html.I(className="fas fa-user me-1"), "Fetch User Profile"],
                                       id="fetch-profile-button", color="secondary", className="me-2",
                                       disabled=not is_authenticated_on_load),
                            width="auto"),
                    dbc.Col(dbc.Button([html.I(className="fas fa-briefcase me-1"), "Fetch Holdings (Mock)"],
                                       id="fetch-holdings-button", color="secondary", disabled=not is_authenticated_on_load),
                            width="auto"),
                ], className="g-2 mb-3 justify-content-start"),
                html.Hr(),
                html.Div(id="profile-output"),
                html.Div(id="holdings-output", className="mt-3"),
                html.H4("Place Order", className="mt-4"),

                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Instrument:", className="mb-0"),
                        dcc.Dropdown(
                            id="instrument-input",
                            options=[{"label": row['tradingsymbol'], "value": row['tradingsymbol']}
                                     for _, row in instrument_df[instrument_df['segment'].isin(['NSE', 'NFO'])].iterrows()],
                            placeholder="Select an instrument",
                            searchable=True
                        ),
                    ]), md=4),
                    dbc.Col(html.Div([
                        dbc.Label("Quantity", className="mb-0"),
                        dbc.Input(id="quantity-input", type="number", min=1, step=1),
                    ]), md=4),
                    dbc.Col(html.Div([
                        dbc.Label("Price (Optional)", className="mb-0"),
                        dbc.Input(id="price-input", type="number", min=0),
                    ]), md=4),
                ], className="g-2 mb-3"),
                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Transaction Type", className="mb-0"),
                        dbc.Select(
                            id="transaction-type-select",
                            options=[{"label": "BUY", "value": "BUY"}, {"label": "SELL", "value": "SELL"}],
                            value="BUY"
                        ),
                    ]), md=6),
                    dbc.Col(html.Div([
                        dbc.Label("Order Type", className="mb-0"),
                        dbc.Select(
                            id="order-type-select",
                            options=[{"label": "MARKET", "value": "MARKET"}, {"label": "LIMIT", "value": "LIMIT"}],
                            value="MARKET"
                        ),
                    ]), md=6),
                ], className="g-2 mb-3"),
                dbc.Button([html.I(className="fas fa-exchange-alt me-1"), "Place Order"],
                           id="place-order-button", color="danger", disabled=not is_authenticated_on_load),
                html.Div(id="order-output", className="mt-3"),
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=3),


        dbc.Col(dbc.Card([
            dbc.CardHeader("Instrument Master & Option Chain"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Button([html.I(className="fas fa-download me-1"), "Load & Save Instrument Data"],
                                       id="load-save-instruments-button", color="info", className="me-2",
                                       disabled=not is_authenticated_on_load),
                            width="auto"),
                ], className="g-2 mb-3 justify-content-start"),
                dcc.Loading(
                    id="loading-instruments",
                    type="circle",
                    children=html.Div(id="instrument-status-output")
                ),
                html.Div(id="csv-status-output", className="mt-2"),
                html.Hr(),
                html.H5("Option Chain Table", className="mb-2"),
                html.Div([
                    html.H6("Configurable Order Parameters:", className="mt-4 mb-2"),
                    dbc.Row([
                        dbc.Col(html.Div([
                            dbc.Label("Trade Mode:", className="mb-0"),
                            dbc.Select(
                                id="oc-trade-mode-select",
                                options=[
                                    {"label": "Normal Order", "value": "NORMAL"},
                                    {"label": "GTT Order", "value": "GTT"},
                                    {"label": "Alert (Price Notification)", "value": "ALERT"}
                                ],
                                value="NORMAL",
                            ),
                        ]), md=4),
                        dbc.Col(html.Div([
                            dbc.Label("Quantity Multiplier (e.g., 3 for 3 lots):", className="mb-0"),
                            dbc.Input(id="quantity-multiplier-input", type="number", value=1, min=1, step=1),
                        ]), md=4),
                        dbc.Col(html.Div([
                            dbc.Label("Order Type:", className="mb-0"),
                            dbc.Select(
                                id="oc-order-type-select",
                                options=[
                                    {"label": "MARKET", "value": "MARKET"},
                                    {"label": "LIMIT", "value": "LIMIT"}
                                ],
                                value="MARKET",
                            ),
                        ]), md=4),
                    ], className="g-2 mb-3"),

                    # Conditional GTT inputs
                    html.Div(id="gtt-inputs-div", style={'display': 'none'}, children=[
                        dbc.Row([
                            dbc.Col(html.Div([
                                dbc.Label("GTT Type:", className="mb-0"),
                                dbc.Select(
                                    id="oc-gtt-type-select",
                                    options=[
                                        {"label": "Single Leg", "value": "SINGLE"},
                                        {"label": "Two Leg (SL & Target)", "value": "TWO_LEG"}
                                    ],
                                    value="SINGLE",
                                ),
                            ]), md=6),
                        ], className="g-2 mb-3"),
                        html.Div(id="gtt-single-trigger-input-div", children=[
                            dbc.Row([
                                dbc.Col(html.Div([
                                    dbc.Label("GTT Trigger Price:", className="mb-0"),
                                    dbc.Input(id="gtt-trigger-price-input", type="number", placeholder="Enter GTT trigger price"),
                                ]), md=6),
                            ], className="g-2 mb-3"),
                        ]),
                        html.Div(id="gtt-two-leg-trigger-input-div", style={'display': 'none'}, children=[
                            dbc.Row([
                                dbc.Col(html.Div([
                                    dbc.Label("SL Trigger Price:", className="mb-0"),
                                    dbc.Input(id="gtt-sl-price-input", type="number", placeholder="Enter Stop Loss trigger price"),
                                ]), md=6),
                                dbc.Col(html.Div([
                                    dbc.Label("Target Trigger Price:", className="mb-0"),
                                    dbc.Input(id="gtt-target-price-input", type="number", placeholder="Enter Target trigger price"),
                                ]), md=6),
                            ], className="g-2 mb-3"),
                        ]),
                    ]),

                    # Conditional Alert inputs
                    html.Div(id="alert-inputs-div", style={'display': 'none'}, children=[
                        dbc.Row([
                            dbc.Col(html.Div([
                                dbc.Label("Alert Trigger Price:", className="mb-0"),
                                dbc.Input(id="alert-trigger-price-input", type="number", placeholder="Enter alert trigger price"),
                            ]), md=6),
                            dbc.Col(html.Div([
                                dbc.Label("Alert Trigger Type:", className="mb-0"),
                                dbc.Select(
                                    id="alert-trigger-type-select",
                                    options=[
                                        {"label": "LTP Cross Above", "value": "ltp_cross_above"},
                                        {"label": "LTP Cross Below", "value": "ltp_cross_below"}
                                    ],
                                    value="ltp_cross_above",
                                ),
                            ]), md=6),
                        ], className="g-2 mb-3"),
                    ]),

                    dbc.Row([
                        dbc.Col(html.Div([
                            dbc.Label("Limit Price (required for LIMIT orders):", className="mb-0"),
                            dbc.Input(id="oc-price-input", type="number", placeholder="Enter limit price", disabled=True),
                        ]), md=6),
                        dbc.Col(html.Div([
                            dbc.Label("Product Type:", className="mb-0"),
                            dbc.Select(
                                id="oc-product-type-select",
                                options=[
                                    {"label": "MIS (Intraday)", "value": "MIS"},
                                    #{"label": "CNC (Delivery/Equity)", "value": "CNC"},
                                    {"label": "NRML (Carry Forward)", "value": "NRML"}
                                ],
                                value="MIS", # Default to MIS for options
                            ),
                        ]), md=6),
                    ], className="g-2 mb-3"),
                ], className="border rounded p-3 mb-4"), # Styling for the new section

                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Select Underlying Index:", className="mb-0"),
                        dbc.Select(
                            id="index-select",
                            options=[
                                {"label": "NIFTY", "value": "NIFTY"},
                                {"label": "BANKNIFTY", "value": "BANKNIFTY"},
                                {"label": "FINNIFTY", "value": "FINNIFTY"},
                                {"label": "MIDCPNIFTY", "value": "MIDCPNIFTY"},
                                {"label": "SENSEX", "value": "SENSEX"},
                                {"label": "BANKEX", "value": "BANKEX"}
                            ],
                            value="NIFTY" # Default selection
                        ),
                    ]), md=6),
                    dbc.Col(html.Div([
                        dbc.Label("Select Expiry Date:", className="mb-0"),
                        dbc.Select(
                            id='option-expiry-select',
                            placeholder="Select an Expiry Date",
                            options=[], # Options will be populated dynamically
                            value=None
                        ),
                    ]), md=6),
                ], className="g-2 mb-3"),
                # New input for number of OTM strikes
                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Number of OTM Strikes (each side):", className="mb-0"),
                        dbc.Input(id="num-otm-strikes-input", type="number", value=5, min=0, step=1),
                    ]), md=6),
                ], className="g-2 mb-3"),
                dbc.Button([html.I(className="fas fa-table me-1"), "Generate Option Chain Table"],
                           id="plot-option-chain-button", color="success", disabled=not is_authenticated_on_load),
                dcc.Loading(
                    id="loading-option-chain",
                    type="circle",
                    children=html.Div(id="option-chain-status-output")
                ),
                html.Div(id="option-chain-table", className="mt-3"),
                # Use dcc.Graph for Plotly charts
                dcc.Graph(id="option-chain-chart", style={"height": "400px"}),
                html.Div(id="oc-order-output", className="mt-3"),
                # Removed Previous/Next buttons for pagination as requested
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=9),
    ], className="mb-4 g-3"),
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Live Market Quotes (LTP)"),
            dbc.CardBody([
                html.Div([
                    dbc.Label("Enter Trading Symbols (comma-separated):", className="mb-0"),
                    dbc.Input(id="quote-symbols-input", type="text", placeholder="NSE:INFY,NFO:NIFTY25JUN19500CE",
                              className="mb-3"),
                ]),
                dbc.Button([html.I(className="fas fa-chart-line me-1"), "Fetch Live LTP"],
                           id="fetch-quotes-button", color="primary", disabled=not is_authenticated_on_load),
                dcc.Loading(
                    id="loading-quotes",
                    type="circle",
                    children=html.Div(id="quotes-status-output")
                ),
                html.Div(id="market-quotes-output", className="mt-3")
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=12),
    ], className="mb-4 g-3")
], fluid=True, id="container", style={"fontFamily": "Inter, sans-serif", "margin": "auto"}) # Set font and center container

# --- Dash Callbacks ---

@app.callback(
    Output("container", "className"),
    Input("dark-mode-toggle", "value")
)
def toggle_dark_mode(dark_mode):
    """Toggles dark mode class on the main container."""
    return "dark-theme" if dark_mode else "light-theme"

@app.callback(
    Output("api-key-input", "valid"),
    Output("api-key-input", "invalid"),
    Input("api-key-input", "value")
)
def validate_api_key(api_key):
    """Validates if the entered API key is of a reasonable length."""
    if api_key and len(api_key) > 10: # Basic validation for length
        return True, False
    return False, True

@app.callback(
    [Output("login-url-output", "children"),
     Output("open-login-button", "disabled", allow_duplicate=True),
     Output("init-kite-button", "disabled", allow_duplicate=True),
     Output("clear-token-button", "disabled", allow_duplicate=True),
     Output("notification-toast", "is_open", allow_duplicate=True),
     Output("notification-toast", "children", allow_duplicate=True),
     Output('interval-component', 'disabled', allow_duplicate=True)], # Control interval based on auth
    Input("init-kite-button", "n_clicks"),
    Input("clear-token-button", "n_clicks"),
    State("api-key-input", "value"),
    State("api-secret-input", "value"),
    State("redirect-url-input", "value"),
    prevent_initial_call=True
)
def handle_kite_init_and_clear(init_n_clicks, clear_n_clicks, api_key, api_secret, redirect_url):
    """
    Handles initialization of KiteConnect and clearing of saved tokens.
    Also manages starting/stopping the WebSocket and clearing live data.
    """
    global kite, access_token, user_profile, instrument_df
    ctx = dash.callback_context
    if not ctx.triggered:
        return [dash.no_update] * 7

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if button_id == "clear-token-button" and clear_n_clicks:
        # Clear saved token and global variables
        set_key('.env', "KITE_ACCESS_TOKEN", "")
        access_token = None
        kite = None
        user_profile = None
        instrument_df = pd.DataFrame() # Clear instrument data as it depends on API key
        kitews.stop_websocket() # Stop the WebSocket connection
        kitews.clear_live_quotes() # Clear cached live quotes
        return (html.P("Saved token cleared. Please re-authenticate.", className="text-info"),
                False, False, True, True, "Token cleared successfully.", True) # Disable interval

    elif button_id == "init-kite-button" and init_n_clicks:
        try:
            # Update global config variables and initialize KiteConnect instance
            global KITE_API_KEY, KITE_API_SECRET, KITE_REDIRECT_URL
            KITE_API_KEY = api_key
            KITE_API_SECRET = api_secret
            KITE_REDIRECT_URL = redirect_url
            kite = KiteConnect(api_key=KITE_API_KEY)
            login_url = kite.login_url() # Get the login URL for user
            return ([html.P(f"Kite Connect initialized. Login URL generated:"),
                     html.A(login_url, href=login_url, target="_blank", rel="noopener noreferrer", className="alert-link")],
                    False, True, False, True, "Kite initialized successfully. Click 'Open Kite Login Page'.", True) # Keep interval disabled
        except Exception as e:
            return (html.P(f"Error initializing Kite: {e}", className="text-danger"),
                    True, False, True, True, f"Error: {e}", True) # Keep interval disabled
    return [dash.no_update] * 7 # Should not happen if logic is correct

@app.callback(
    Output("open-login-button", "n_clicks"),
    Input("open-login-button", "n_clicks"),
    State("login-url-output", "children"),
    prevent_initial_call=True
)
def open_login_page(n_clicks, login_url_element):
    """Opens the Kite login URL in a new browser tab."""
    if n_clicks and login_url_element and isinstance(login_url_element, list) and len(login_url_element) > 1:
        # Check if the second element in children is an A tag with an href
        if hasattr(login_url_element[1], 'href'):
            webbrowser.open_new_tab(login_url_element[1].href)
    return dash.no_update

@app.callback(
    [Output("access-token-output", "children"),
     Output("fetch-profile-button", "disabled", allow_duplicate=True),
     Output("fetch-holdings-button", "disabled", allow_duplicate=True),
     Output("place-order-button", "disabled", allow_duplicate=True),
     Output("load-save-instruments-button", "disabled", allow_duplicate=True),
     Output("plot-option-chain-button", "disabled", allow_duplicate=True),
     Output("fetch-quotes-button", "disabled", allow_duplicate=True),
     Output("init-kite-button", "disabled", allow_duplicate=True),
     Output("open-login-button", "disabled", allow_duplicate=True),
     Output("clear-token-button", "disabled", allow_duplicate=True),
     Output("generate-token-button", "disabled", allow_duplicate=True),
     Output("notification-toast", "is_open", allow_duplicate=True),
     Output("notification-toast", "children", allow_duplicate=True),
     Output('interval-component', 'disabled', allow_duplicate=True)], # Control interval based on auth
    Input("generate-token-button", "n_clicks"),
    State("request-token-input", "value"),
    State("api-secret-input", "value"),
    prevent_initial_call=True
)
def generate_access_token(n_clicks, request_token, api_secret):
    """
    Generates an access token using the request token and API secret,
    and then initializes the KiteConnect object with it.
    Also starts the WebSocket connection.
    """
    global access_token, user_profile, kite
    if n_clicks and request_token and kite:
        try:
            data = kite.generate_session(request_token, api_secret=api_secret)
            access_token = data["access_token"]
            user_profile = data["user_name"]
            kite.set_access_token(access_token) # Set access token for REST API calls
            set_key('.env', "KITE_ACCESS_TOKEN", access_token) # Save token to .env for persistence

            # Get all index tokens to subscribe permanently
            all_index_tokens_for_ws = [details["instrument_token"] for details in INDEX_INSTRUMENT_DETAILS.values()]
            kitews.start_websocket(KITE_API_KEY, access_token, initial_tokens=all_index_tokens_for_ws)

            return (html.Div([
                html.P("Access Token generated and saved successfully!"),
                html.P(f"User: {user_profile}"),
                html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
            ], className="text-success"),
            False, False, False, False, False, False, True, True, False, True, # Enable/disable buttons
            True, "Access token generated successfully.", False) # Enable interval
        except Exception as e:
            access_token = None
            user_profile = None
            # On error, ensure everything is disabled and WebSocket is stopped
            kitews.stop_websocket()
            kitews.clear_live_quotes()
            return (html.P(f"Error generating access token: {e}", className="text-danger"),
                   True, True, True, True, True, True, False, False, True, False, # Disable all buttons
                   True, f"Error: {e}", True) # Disable interval
    return [dash.no_update] * 14 # Default return for no action

@app.callback(
    [Output("fetch-profile-button", "disabled", allow_duplicate=True),
     Output("fetch-holdings-button", "disabled", allow_duplicate=True),
     Output("place-order-button", "disabled", allow_duplicate=True),
     Output("load-save-instruments-button", "disabled", allow_duplicate=True),
     Output("plot-option-chain-button", "disabled", allow_duplicate=True),
     Output("fetch-quotes-button", "disabled", allow_duplicate=True),
     Output("init-kite-button", "disabled", allow_duplicate=True),
     Output("open-login-button", "disabled", allow_duplicate=True),
     Output("clear-token-button", "disabled", allow_duplicate=True),
     Output("generate-token-button", "disabled", allow_duplicate=True),
     Output("access-token-output", "children", allow_duplicate=True),
     Output('interval-component', 'disabled', allow_duplicate=True)], # Control interval based on auth
    Input('access-token-output', 'children'), # Triggered by the output of generate_access_token
    prevent_initial_call='callback_args_grouping' # Prevents this from firing on app load without an explicit trigger
)
def update_button_states_on_load_or_token_change(access_token_output_children_input_value):
    """
    Updates the disabled state of various buttons based on whether an access token is present.
    This callback ensures buttons are enabled/disabled correctly on initial load or after token changes.
    """
    is_authenticated = bool(access_token)
    display_message = html.P("No valid access token found. Please initialize Kite and authenticate.", className="text-info")
    if is_authenticated and user_profile:
        display_message = html.Div([
            html.P("Loaded with saved Access Token!"),
            html.P(f"User: {user_profile}"),
            html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
        ], className="text-success")
    elif is_authenticated:
        display_message = html.Div([
            html.P("Loaded with saved Access Token (profile not fetched yet)!"),
            html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
        ], className="text-success")
    return (
        not is_authenticated, not is_authenticated, not is_authenticated, # Dashboard buttons
        not is_authenticated, not is_authenticated, not is_authenticated, # Instrument/Quotes buttons
        is_authenticated, is_authenticated, not is_authenticated, is_authenticated, # Auth buttons
        display_message,
        not is_authenticated # Disable interval if not authenticated
    )

@app.callback(
    Output("profile-output", "children"),
    Input("fetch-profile-button", "n_clicks"),
    prevent_initial_call=True
)
def fetch_user_profile(n_clicks):
    """Fetches and displays the user's profile information."""
    if n_clicks and kite and access_token:
        try:
            profile = kite.profile()
            return html.Div([
                html.H5("User Profile:"),
                html.P(f"User Name: {profile.get('user_name')}"),
                html.P(f"User ID: {profile.get('user_id')}"),
                html.P(f"Email: {profile.get('email')}"),
                html.P(f"Broker: {profile.get('broker')}")
            ])
        except Exception as e:
            return html.P(f"Error fetching profile: {e}", className="text-danger")
    return ""

@app.callback(
    Output("holdings-output", "children"),
    Input("fetch-holdings-button", "n_clicks"),
    prevent_initial_call=True
)
def fetch_holdings(n_clicks):
    """Fetches and displays mock user holdings."""
    if n_clicks and kite and access_token:
        try:
            # Using mock data as actual holdings fetch requires a separate API call
            # which might involve stricter permissions or specific KiteConnect methods
            mock_holdings = [
                {"instrument": "RELIANCE", "quantity": 10, "avg_cost": 2500.50},
                {"instrument": "TCS", "quantity": 5, "avg_cost": 3800.25},
                {"instrument": "INFY", "quantity": 15, "avg_cost": 1500.00},
            ]
            return html.Div([
                html.H5("Your Holdings (Mock Data):"),
                dbc.Table.from_dataframe(
                    pd.DataFrame(mock_holdings),
                    bordered=True, hover=True, striped=True, className="mt-2"
                )
            ])
        except Exception as e:
            return html.P(f"Error fetching holdings: {e}", className="text-danger")
    return ""

@app.callback(
    Output("order-output", "children"),
    Input("place-order-button", "n_clicks"),
    State("instrument-input", "value"),
    State("quantity-input", "value"),
    State("price-input", "value"),
    State("transaction-type-select", "value"),
    State("order-type-select", "value"),
    prevent_initial_call=True
)
def place_order(n_clicks, instrument, quantity, price, transaction_type, order_type):
    """Places an actual order using KiteConnect API."""
    if n_clicks and instrument and quantity and kite and access_token:
        try:
            # Determine exchange and product type (simplified for this example)
            # In a real app, you'd likely fetch instrument details to get the exchange,
            # and allow user to select product (CNC, MIS, NRML)
            exchange = "NSE" # Defaulting for equities
            product = kite.PRODUCT_CNC # Defaulting for delivery orders

            if order_type == "LIMIT" and not price:
                return html.P("Price is required for LIMIT orders.", className="text-danger")
            if order_type == "MARKET":
                price = None # Price is not applicable for market orders

            # Use kite.place_order() with appropriate parameters
            order_id = kite.place_order(
                tradingsymbol=instrument,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=int(quantity),
                order_type=order_type,
                product=product,
                price=float(price) if price else None, # Pass price only if not market order
                variety=kite.VARIETY_REGULAR, # Standard order
                validity=kite.VALIDITY_DAY # Valid for the day
            )
            return html.P(
                f"Order placed for {quantity} shares of {instrument} ({transaction_type} {order_type}). Order ID: {order_id}",
                className="text-success"
            )
        except Exception as e:
            return html.P(f"Error placing order: {e}", className="text-danger")
    return ""


@app.callback(
    [Output("instrument-status-output", "children"),
     Output("csv-status-output", "children"),
     Output("notification-toast", "is_open", allow_duplicate=True),
     Output("notification-toast", "children", allow_duplicate=True)],
    Input("load-save-instruments-button", "n_clicks"),
    prevent_initial_call=True
)
def load_and_save_instrument_master(n_clicks):
    """
    Fetches instrument master data from Kite, saves it to CSV and SQLite,
    and updates the global instrument_df.
    """
    global instrument_df
    if n_clicks and kite and access_token:
        try:
            instruments = kite.instruments() # Fetch all instruments
            instrument_df = pd.DataFrame(instruments) # Convert to DataFrame
            instrument_df.to_csv(INSTRUMENT_CSV_PATH, index=False) # Save to CSV
            
            # Save to SQLite
            conn = sqlite3.connect(INSTRUMENT_DB_PATH)
            instrument_df.to_sql('instruments', conn, if_exists='replace', index=False)
            conn.close()

            return (html.P(f"Instrument master loaded. Total instruments: {len(instrument_df)}", className="text-success"),
                    html.P(f"Instrument data saved to {INSTRUMENT_CSV_PATH} and SQLite", className="text-success"),
                    True, "Instruments loaded and saved successfully.")
        except Exception as e:
            instrument_df = pd.DataFrame() # Clear df on error
            return (html.P(f"Error loading instrument master: {e}", className="text-danger"),
                    html.P("Failed to save instrument data.", className="text-warning"),
                    True, f"Error: {e}")
    return "", "", False, ""

@app.callback(
    Output('oc-price-input', 'disabled'),
    Input('oc-order-type-select', 'value'),
    prevent_initial_call=False
)
def toggle_oc_price_input(order_type):
    """Disables the limit price input if order type is MARKET."""
    return order_type == 'MARKET'

@app.callback(
    [Output('option-expiry-select', 'options'),
     Output('option-expiry-select', 'value')],
    [Input('index-select', 'value'),
     Input('load-save-instruments-button', 'n_clicks')], # Re-trigger if instruments loaded/saved
    prevent_initial_call=False
)
def update_expiry_dropdown(selected_index, load_save_instruments_n_clicks):
    """
    Populates the expiry date dropdown based on the selected underlying index
    and available instrument data.
    """
    if selected_index and not instrument_df.empty:
        # Filter instruments for options of the selected index
        options_for_index = instrument_df[
            (instrument_df['instrument_type'].isin(['CE', 'PE'])) &
            (instrument_df['name'] == selected_index.upper()) &
            (instrument_df['exchange'].isin(['NFO', 'BFO'])) # NFO/BFO for F&O
        ].copy()
        
        if options_for_index.empty:
            return [], None
        
        # Extract unique expiry dates, convert to datetime objects, and sort
        parsed_expiries = pd.to_datetime(options_for_index['expiry'], errors='coerce').dt.date
        unique_expiries_dt = parsed_expiries.dropna().unique()
        
        # Filter out past expiries and sort them
        valid_expiries = sorted([dt for dt in unique_expiries_dt if dt >= datetime.now().date()])
        
        # Format for dropdown options
        expiry_options = [{"label": dt.strftime('%d-%m-%Y'), "value": dt.strftime('%Y-%m-%d')} for dt in valid_expiries]
        default_expiry_value = expiry_options[0]['value'] if expiry_options else None
        return expiry_options, default_expiry_value
    return [], None

# This callback should be present in kite5.py to make the above divs visible
@app.callback(
    Output('gtt-inputs-div', 'style'),
    Output('alert-inputs-div', 'style'),
    Input('oc-trade-mode-select', 'value'),
)
def toggle_trade_mode_inputs(trade_mode):
    gtt_style = {'display': 'none'}
    alert_style = {'display': 'none'}

    if trade_mode == 'GTT':
        gtt_style = {'display': 'block'}
    elif trade_mode == 'ALERT':
        alert_style = {'display': 'block'}
    
    return gtt_style, alert_style

@app.callback(
    [Output("option-chain-table", "children"),
     Output("option-chain-status-output", "children"),
     Output("option-chain-chart", "figure"),
     Output("notification-toast", "is_open", allow_duplicate=True),
     Output("notification-toast", "children", allow_duplicate=True)],
    [Input("plot-option-chain-button", "n_clicks"),
     Input('interval-component', 'n_intervals')], # Add interval as an input for live updates
    [State("index-select", "value"),
     State("option-expiry-select", "value"),
     State("num-otm-strikes-input", "value")], # State for OTM strikes
    prevent_initial_call=True
)
def plot_option_chain(plot_clicks, n_intervals,
                      underlying_symbol, expiry_date_str, num_otm_strikes):
    """
    Generates and updates the option chain table and chart using live tick data from WebSocket.
    Displays a fixed window of strikes centered around the ATM.
    """
    global instrument_df
    ctx = dash.callback_context
    
    # Ensure num_otm_strikes is an integer and non-negative, default to 5 if invalid
    if num_otm_strikes is None or not isinstance(num_otm_strikes, int) or num_otm_strikes < 0:
        num_otm_strikes = 5 # Default value
    
    # Calculate the total number of strikes to display (2 * OTM + ATM)
    display_window_size = (2 * num_otm_strikes) + 1

    # Check for initial conditions
    if not ctx.triggered or not kite or not access_token or instrument_df.empty:
        return None, html.P("Please load instrument master and ensure you are logged in.", className="text-warning"), {}, False, ""

    # --- Initial data fetching for underlying and options to get 'strikes' list ---
    if not underlying_symbol or not expiry_date_str:
        return None, html.P("Please select an underlying index and expiry date.", className="text-warning"), {}, False, ""

    try:
        expiry_date_dt = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
        
        # Get underlying instrument token and strike step from predefined details
        index_details = INDEX_INSTRUMENT_DETAILS.get(underlying_symbol.upper())
        if not index_details:
            return None, html.P(f"Details for index {underlying_symbol} not found.", className="text-danger"), {}, True, "Error: Index details missing."

        underlying_token = index_details["instrument_token"]
        strike_step = index_details["strike_step"]

        current_ltp = None
        # Always include all index tokens for subscription
        instruments_to_subscribe = [details["instrument_token"] for details in INDEX_INSTRUMENT_DETAILS.values()]

        # Try to get underlying LTP from WebSocket live_quotes first, with retries
        max_retries = 3
        retry_delay_sec = 0.5
        for attempt in range(max_retries):
            ltp_data_from_ws = kitews.get_live_quote(underlying_token)
            if ltp_data_from_ws and 'last_price' in ltp_data_from_ws:
                current_ltp = ltp_data_from_ws['last_price']
                break # Exit loop if data is found
            else:
                print(f"WebSocket data for {underlying_symbol} not found (attempt {attempt+1}/{max_retries}), retrying in {retry_delay_sec}s...")
                time.sleep(retry_delay_sec)

        if current_ltp is None:
            # Fallback to REST API (kite.ltp) if WebSocket data is still not available after retries
            print(f"WebSocket data for {underlying_symbol} still not found after retries, falling back to REST API for initial LTP.")
            try:
                # Need the exchange and tradingsymbol for kite.ltp
                underlying_instrument_info = instrument_df[instrument_df['instrument_token'] == underlying_token].iloc[0]
                rest_ltp_data = kite.ltp([f"{underlying_instrument_info['exchange']}:{underlying_instrument_info['tradingsymbol']}"])
                if rest_ltp_data and f"{underlying_instrument_info['exchange']}:{underlying_instrument_info['tradingsymbol']}" in rest_ltp_data:
                    current_ltp = rest_ltp_data[f"{underlying_instrument_info['exchange']}:{underlying_instrument_info['tradingsymbol']}"]['last_price']
            except Exception as ltp_e:
                print(f"Could not fetch LTP for {underlying_symbol} via REST API: {ltp_e}")

        # Filter all options for the selected underlying and expiry
        filtered_options = instrument_df[
            (instrument_df['instrument_type'].isin(['CE', 'PE'])) &
            (instrument_df['name'] == underlying_symbol.upper()) &
            (instrument_df['exchange'].isin(['NFO', 'BFO']))
        ].copy()
        filtered_options['expiry_dt'] = pd.to_datetime(filtered_options['expiry'], errors='coerce').dt.date
        options_for_expiry = filtered_options[filtered_options['expiry_dt'] == expiry_date_dt]

        if options_for_expiry.empty:
            return None, html.P(f"No options found for {underlying_symbol.upper()} with expiry {expiry_date_str}", className="text-warning"), {}, False, ""

        # Corrected filtering for calls and puts to avoid UnboundLocalError
        calls = options_for_expiry[options_for_expiry['instrument_type'] == 'CE'].sort_values(by='strike')
        puts = options_for_expiry[options_for_expiry['instrument_type'] == 'PE'].sort_values(by='strike')
        strikes = sorted(list(options_for_expiry['strike'].unique()))

        # --- Windowing Logic based on OTM Strikes (No Pagination) ---
        display_strikes = []
        if current_ltp is not None:
            # Calculate the ATM strike by rounding LTP to the nearest strike_step
            atm_strike = round(current_ltp / strike_step) * strike_step

            # Find the index of the ATM strike in the sorted strikes list
            try:
                atm_idx_in_all_strikes = strikes.index(atm_strike)
            except ValueError:
                # If ATM strike is not perfectly in the list, find the closest one
                atm_strike = min(strikes, key=lambda x: abs(x - current_ltp))
                atm_idx_in_all_strikes = strikes.index(atm_strike)

            # Determine the start and end indices for the display window
            start_idx = max(0, atm_idx_in_all_strikes - num_otm_strikes)
            end_idx = min(len(strikes), atm_idx_in_all_strikes + num_otm_strikes + 1) # +1 to include ATM and num_otm_strikes above

            # Adjust window if it goes out of bounds at the end
            if (end_idx - start_idx) < display_window_size:
                start_idx = max(0, end_idx - display_window_size)
            
            display_strikes = strikes[start_idx:end_idx]
            
            # Ensure we display exactly `display_window_size` strikes if possible
            if len(display_strikes) < display_window_size and len(strikes) >= display_window_size:
                 # If we couldn't get enough by adjusting start_idx, take from the beginning if that makes sense
                 display_strikes = strikes[0:display_window_size]


        if not display_strikes and strikes: # Fallback if for some reason no ATM found or strikes list is too small
            display_strikes = strikes[0:min(len(strikes), display_window_size)]
        
        if not display_strikes:
             return None, html.P("No strikes to display based on criteria.", className="text-warning"), {}, False, ""


        # Filter calls and puts to only include the strikes to be displayed
        calls = calls[calls['strike'].isin(display_strikes)]
        puts = puts[puts['strike'].isin(display_strikes)]

        # Collect all instrument tokens for displayed options for WebSocket subscription
        for _, row in calls.iterrows():
            instruments_to_subscribe.append(row['instrument_token'])
        for _, row in puts.iterrows():
            instruments_to_subscribe.append(row['instrument_token'])
        
        # Subscribe to all unique relevant tokens for live updates via WebSocket
        # This will ensure index tokens are always subscribed, plus the displayed options
        if instruments_to_subscribe:
            kitews.subscribe_to_tokens(list(set(instruments_to_subscribe)))

        # Get the latest quotes for all instruments from the WebSocket's live_quotes cache
        quotes = kitews.get_all_live_quotes()

        # --- Build Option Chain Table ---
        header_row_1 = html.Tr([
            html.Th(children="CALLS", className="text-center call-col", colSpan=6),
            html.Th(children="Strike", className="text-center strike-col", rowSpan=2),
            html.Th(children="PUTS", className="text-center put-col", colSpan=6),
        ])
        header_row_2 = html.Tr([
            html.Th("OI"), html.Th("Vol"), html.Th("Bid"), html.Th("Ask"), html.Th("LTP"), html.Th("Act"),
            html.Th("LTP"), html.Th("Bid"), html.Th("Ask"), html.Th("Vol"), html.Th("OI"), html.Th("Act")
        ])
        table_body_rows = []
        for strike in display_strikes:
            # Safely get call and put option as dictionaries
            # Convert Series to dict if not empty, otherwise provide an empty dict
            call_option = calls[calls['strike'] == strike].iloc[0].to_dict() if not calls[calls['strike'] == strike].empty else {}
            put_option = puts[puts['strike'] == strike].iloc[0].to_dict() if not puts[puts['strike'] == strike].empty else {}
            
            # Get quote data using instrument_token from the live_quotes cache
            # If call_option/put_option is {}, .get('instrument_token') will return None,
            # which quotes.get() can handle by returning the default value ({})
            quote_ce = quotes.get(call_option.get('instrument_token'), {})
            quote_pe = quotes.get(put_option.get('instrument_token'), {})

            call_itm_class = "itm-call" if current_ltp is not None and strike < current_ltp else ""
            put_itm_class = "itm-put" if current_ltp is not None and strike > current_ltp else ""
            
            # Safely get and format LTP, handling cases where data might be missing
            call_ltp_display = f"{quote_ce.get('last_price', '-'):.2f}" if isinstance(quote_ce.get('last_price'), (int, float)) else '-'
            put_ltp_display = f"{quote_pe.get('last_price', '-'):.2f}" if isinstance(quote_pe.get('last_price'), (int, float)) else '-'

            # Safely extract depth data (bid/ask prices)
            call_bid_price = quote_ce.get('depth', {}).get('buy', [{}])[0].get('price', '-')
            call_ask_price = quote_ce.get('depth', {}).get('sell', [{}])[0].get('price', '-')
            put_bid_price = quote_pe.get('depth', {}).get('buy', [{}])[0].get('price', '-')
            put_ask_price = quote_pe.get('depth', {}).get('sell', [{}])[0].get('price', '-')

            row_cells = [
                html.Td(quote_ce.get('oi', '-'), className=call_itm_class),
                html.Td(quote_ce.get('volume', '-'), className=call_itm_class),
                html.Td(call_bid_price, className=call_itm_class),
                html.Td(call_ask_price, className=call_itm_class),
                html.Td(call_ltp_display, className=call_itm_class),
                html.Td([
                    dbc.Button("B", id={'type': 'oc-order-button', 'action': 'BUY', 'tradingsymbol': str(call_option.get('tradingsymbol')), 'exchange': str(call_option.get('exchange')), 'lot_size': str(call_option.get('lot_size')), 'last_price': str(quote_ce.get('last_price')), 'strike': str(call_option.get('strike')), 'instrument_type': str(call_option.get('instrument_type'))}, color="success", size="sm", className="btn-oc me-1", disabled=not bool(access_token)),
                    dbc.Button("S", id={'type': 'oc-order-button', 'action': 'SELL', 'tradingsymbol': str(call_option.get('tradingsymbol')), 'exchange': str(call_option.get('exchange')), 'lot_size': str(call_option.get('lot_size')), 'last_price': str(quote_ce.get('last_price')), 'strike': str(call_option.get('strike')), 'instrument_type': str(call_option.get('instrument_type'))}, color="danger", size="sm", className="btn-oc", disabled=not bool(access_token))
                ], className=call_itm_class),
                html.Td(f"{strike:.2f}", className="strike-col"), # Strike price column
                html.Td(put_ltp_display, className=put_itm_class),
                html.Td(put_bid_price, className=put_itm_class),
                html.Td(put_ask_price, className=put_itm_class),
                html.Td(quote_pe.get('volume', '-'), className=put_itm_class),
                html.Td(quote_pe.get('oi', '-'), className=put_itm_class),
                html.Td([
                    dbc.Button("B", id={'type': 'oc-order-button', 'action': 'BUY', 'tradingsymbol': str(put_option.get('tradingsymbol')), 'exchange': str(put_option.get('exchange')), 'lot_size': str(put_option.get('lot_size')), 'last_price': str(quote_pe.get('last_price')), 'strike': str(put_option.get('strike')), 'instrument_type': str(put_option.get('instrument_type'))}, color="success", size="sm", className="btn-oc me-1", disabled=not bool(access_token)),
                    dbc.Button("S", id={'type': 'oc-order-button', 'action': 'SELL', 'tradingsymbol': str(put_option.get('tradingsymbol')), 'exchange': str(put_option.get('exchange')), 'lot_size': str(put_option.get('lot_size')), 'last_price': str(quote_pe.get('last_price')), 'strike': str(put_option.get('strike')), 'instrument_type': str(put_option.get('instrument_type'))}, color="danger", size="sm", className="btn-oc", disabled=not bool(access_token))
                ], className=put_itm_class),
            ]
            table_body_rows.append(html.Tr(row_cells))

        oc_table = dbc.Table(
            [html.Thead([header_row_1, header_row_2]), html.Tbody(table_body_rows)],
            bordered=True, responsive=True, className="oc-table mt-3"
        )

        # --- Build Option Chain Chart (using Plotly Graph Objects) ---
        # Get OI data directly from live_quotes cache for displayed strikes
        # Ensure that `call_option.get('instrument_token')` returns None if call_option is empty
        call_oi = [quotes.get(call_option.get('instrument_token'), {}).get('oi', 0)
                   for strike in display_strikes
                   for _, call_option_series in calls[calls['strike'] == strike].iterrows()
                   for call_option in [call_option_series.to_dict()]]
        
        put_oi = [quotes.get(put_option.get('instrument_token'), {}).get('oi', 0)
                  for strike in display_strikes
                  for _, put_option_series in puts[puts['strike'] == strike].iterrows()
                  for put_option in [put_option_series.to_dict()]]

        fig = go.Figure()
        fig.add_trace(go.Bar(x=[f"{strike:.2f}" for strike in display_strikes], y=call_oi, name='Call OI', marker_color='rgba(40, 167, 69, 0.6)')) # Green for Calls
        fig.add_trace(go.Bar(x=[f"{strike:.2f}" for strike in display_strikes], y=put_oi, name='Put OI', marker_color='rgba(220, 53, 69, 0.6)')) # Red for Puts
        
        fig.update_layout(
            title=f'{underlying_symbol} Option Chain OI - Expiry {expiry_date_str}',
            xaxis_title='Strike Price',
            yaxis_title='Open Interest',
            barmode='group', # Bars side-by-side
            hovermode="x unified", # Better hover experience
            template="plotly_white" # Clean theme
        )

        return oc_table, html.P("Option chain table and chart generated successfully.", className="text-success"), fig, False, "Option chain generated successfully."
        #return oc_table, html.P("Option chain table and chart generated successfully.", className="text-success"), fig, True, ""

    except Exception as e:
        import traceback
        traceback.print_exc() # Print full traceback for debugging purposes
        return None, html.P(f"Error generating option chain: {e}", className="text-danger"), {}, True, f"Error: {e}"

@app.callback(
    Output('gtt-single-trigger-input-div', 'style'),
    Output('gtt-two-leg-trigger-input-div', 'style'),
    Input('oc-gtt-type-select', 'value'),
)
def toggle_gtt_type_inputs(gtt_type):
    single_style = {'display': 'none'}
    two_leg_style = {'display': 'none'}

    if gtt_type == 'SINGLE':
        single_style = {'display': 'block'}
    elif gtt_type == 'TWO_LEG':
        two_leg_style = {'display': 'block'}
    
    return single_style, two_leg_style


@app.callback(
    Output('oc-order-output', 'children'),
    Input({'type': 'oc-order-button', 'action': ALL, 'tradingsymbol': ALL, 'exchange': ALL, 'lot_size': ALL, 'last_price': ALL, 'strike': ALL, 'instrument_type': ALL}, 'n_clicks'),
    State("quantity-multiplier-input", "value"), # New State
    State("oc-trade-mode-select", "value"),      # New State
    State("oc-gtt-type-select", "value"),        # New State for GTT type
    State("oc-order-type-select", "value"),      # New State
    State("oc-product-type-select", "value"),    # New State
    State("oc-price-input", "value"),            # New State
    State("gtt-trigger-price-input", "value"),   # New State for single GTT
    State("gtt-sl-price-input", "value"),        # New State for two-leg GTT (SL)
    State("gtt-target-price-input", "value"),    # New State for two-leg GTT (Target)
    State("alert-trigger-price-input", "value"), # New State
    State("alert-trigger-type-select", "value"), # New State
    prevent_initial_call=True
)
def handle_oc_order_button_clicks(n_clicks, quantity_multiplier, oc_trade_mode, oc_gtt_type, oc_order_type, oc_product_type, oc_price, gtt_single_trigger_price, gtt_sl_price, gtt_target_price, alert_trigger_price, alert_trigger_type):
    if not any(n_clicks) or not kite or not access_token:
        return dash.no_update # No button clicked or not authenticated

    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update

    button_data = ctx.triggered_id # Get the dict from pattern matching
    
    if isinstance(button_data, dict):
        action = button_data.get('action')
        tradingsymbol = button_data.get('tradingsymbol')
        exchange = button_data.get('exchange')
        lot_size_str = button_data.get('lot_size') # Get as string
        #last_price_from_button_str = button_data.get('last_price') # Not used directly for order price or GTT condition LTP now
        instrument_type = button_data.get('instrument_type')

        # Convert back to appropriate types from button ID
        try:
            lot_size = int(lot_size_str) if lot_size_str and lot_size_str != 'None' else 0
        except ValueError:
            return html.P("Error: Invalid lot size in instrument details from button.", className="text-danger")

        # Validate configurable parameters
        if quantity_multiplier is None or quantity_multiplier < 1:
            return html.P("Error: Quantity Multiplier must be a positive integer.", className="text-danger")
        
        # Calculate actual quantity
        quantity = int(lot_size * quantity_multiplier)
        if quantity <= 0:
            return html.P("Error: Calculated order quantity is zero or less. Check lot size and multiplier.", className="text-danger")

        # Determine transaction type
        transaction_type_kite = kite.TRANSACTION_TYPE_BUY if action == 'BUY' else kite.TRANSACTION_TYPE_SELL

        # Map product type string to KiteConnect constant
        product_type_map = {
            'MIS': kite.PRODUCT_MIS,
            'CNC': kite.PRODUCT_CNC,
            'NRML': kite.PRODUCT_NRML
        }
        product_type_kite = product_type_map.get(oc_product_type)
        if product_type_kite is None:
            return html.P(f"Error: Invalid product type selected: {oc_product_type}", className="text-danger")

        # Determine order type and price for Normal/GTT orders
        order_type_kite = None
        price_for_order = None # This is the price for the triggered order in GTT/Normal
        if oc_order_type == 'LIMIT':
            order_type_kite = kite.ORDER_TYPE_LIMIT
            price_for_order = float(oc_price) if oc_price is not None else None
            if price_for_order is None or price_for_order <= 0:
                return html.P("Error: Limit Price is required and must be positive for LIMIT orders.", className="text-danger")
        else: # MARKET order
            order_type_kite = kite.ORDER_TYPE_MARKET
            price_for_order = 0.0 # Price for market order in GTT payload is usually 0


        try:
            if oc_trade_mode == 'NORMAL':
                order_id = kite.place_order(
                    variety=kite.VARIETY_REGULAR,
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    transaction_type=transaction_type_kite,
                    quantity=quantity,
                    product=product_type_kite,
                    order_type=order_type_kite,
                    price=price_for_order if oc_order_type == 'LIMIT' else None, # Pass price only for LIMIT normal orders
                    validity=kite.VALIDITY_DAY,
                )
                return html.P(
                    f"Normal Order placed for {quantity} units of {tradingsymbol} ({action} {oc_order_type} {oc_product_type}). Order ID: {order_id}",
                    className="text-success"
                )
            elif oc_trade_mode == 'GTT':
                # Fetch live LTP for the instrument to use as last_price in GTT condition
                ltp_for_gtt_tradingsymbol = f"{exchange}:{tradingsymbol}"
                current_ltp_for_gtt = 0.0
                try:
                    ltp_data = kite.ltp([ltp_for_gtt_tradingsymbol])
                    if ltp_data and ltp_for_gtt_tradingsymbol in ltp_data:
                        current_ltp_for_gtt = ltp_data[ltp_for_gtt_tradingsymbol]['last_price']
                        if current_ltp_for_gtt == 0.0: # If LTP comes as 0, it might be stale or not available
                             return html.P(f"Error: Live LTP for {tradingsymbol} is 0, cannot set GTT. Please check market data.", className="text-danger")
                except Exception as ltp_e:
                    print(f"Warning: Could not fetch live LTP for GTT condition for {tradingsymbol}: {ltp_e}")
                    return html.P(f"Error: Could not fetch live LTP for GTT condition for {tradingsymbol}. Live LTP is required for GTT creation.", className="text-danger")

                if oc_gtt_type == 'SINGLE':
                    if gtt_single_trigger_price is None or gtt_single_trigger_price <= 0:
                        return html.P("Error: GTT Trigger Price is required and must be positive for single-leg GTT orders.", className="text-danger")
                    
                    orders_payload = [{
                        "exchange": exchange,
                        "tradingsymbol": tradingsymbol,
                        "transaction_type": transaction_type_kite, # Same transaction type as button
                        "quantity": quantity,
                        "order_type": order_type_kite,
                        "product": product_type_kite,
                        "price": price_for_order # Price for the order placed when GTT triggers
                    }]
                    
                    gtt_id_response = kite.place_gtt(
                        trigger_type=kite.GTT_TYPE_SINGLE, # Correct GTT type parameter
                        tradingsymbol=tradingsymbol,
                        exchange=exchange,
                        trigger_values=[float(gtt_single_trigger_price)],
                        last_price=current_ltp_for_gtt,
                        orders=orders_payload
                    )
                    return html.P(
                        f"Single-Leg GTT Order created for {quantity} units of {tradingsymbol} (Trigger: {gtt_single_trigger_price}). GTT ID: {gtt_id_response.get('gtt_id', 'N/A')}",
                        className="text-success"
                    )
                
                elif oc_gtt_type == 'TWO_LEG':
                    if gtt_sl_price is None or gtt_sl_price <= 0:
                        return html.P("Error: SL Trigger Price is required and must be positive for two-leg GTT.", className="text-danger")
                    if gtt_target_price is None or gtt_target_price <= 0:
                        return html.P("Error: Target Trigger Price is required and must be positive for two-leg GTT.", className="text-danger")

                    # For two-leg GTT (OCO), the orders are typically inverse of the initial position.
                    # If button action is BUY (for entry), OCO orders should be SELL.
                    # If button action is SELL (for entry), OCO orders should be BUY.
                    oco_transaction_type = kite.TRANSACTION_TYPE_SELL if action == 'BUY' else kite.TRANSACTION_TYPE_BUY

                    # Construct SL order payload
                    sl_order_payload = {
                        "exchange": exchange,
                        "tradingsymbol": tradingsymbol,
                        "transaction_type": oco_transaction_type,
                        "quantity": quantity,
                        "order_type": order_type_kite, # Use selected order type (MARKET/LIMIT)
                        "product": product_type_kite,
                        "price": price_for_order # Use the determined price for order payload
                    }
                    
                    # Construct Target order payload
                    target_order_payload = {
                        "exchange": exchange,
                        "tradingsymbol": tradingsymbol,
                        "transaction_type": oco_transaction_type,
                        "quantity": quantity,
                        "order_type": order_type_kite, # Use selected order type (MARKET/LIMIT)
                        "product": product_type_kite,
                        "price": price_for_order # Use the determined price for order payload
                    }

                    # trigger_values for OCO are [stop_loss_trigger, target_trigger]
                    # orders for OCO are [stop_loss_order, target_order]
                    
                    gtt_id_response = kite.place_gtt(
                        trigger_type=kite.GTT_TYPE_OCO, # Correct GTT type parameter for OCO
                        tradingsymbol=tradingsymbol,
                        exchange=exchange,
                        trigger_values=[float(gtt_sl_price), float(gtt_target_price)],
                        last_price=current_ltp_for_gtt,
                        orders=[sl_order_payload, target_order_payload] # List of two order payloads
                    )
                    return html.P(
                        f"Two-Leg (OCO) GTT Order created for {quantity} units of {tradingsymbol} (SL: {gtt_sl_price}, Target: {gtt_target_price}). GTT ID: {gtt_id_response.get('gtt_id', 'N/A')}",
                        className="text-success"
                    )

                return html.P("Invalid GTT type selected.", className="text-danger")

            elif oc_trade_mode == 'ALERT':
                # Message to clarify the limitation of create_alert vs. type=ato
                return html.Div([
                    html.P(f"Alert requested for {tradingsymbol} (Trigger: {alert_trigger_price} {alert_trigger_type}).", className="text-info"),
                    html.P(f"NOTE: The KiteConnect library's 'create_alert' method primarily creates price *notification* alerts.", className="text-warning"),
                    html.P(f"It does NOT directly support 'Alert to Order' functionality (type=ato) as shown in your curl example for the /alerts API.", className="text-warning"),
                    html.P(f"For order-triggering conditions, please use the 'GTT Order' mode.", className="text-info")
                ])

            return html.P("Invalid trade mode selected.", className="text-danger")

        except Exception as e:
            return html.P(f"Error placing order/creating GTT/alert for {tradingsymbol}: {e}", className="text-danger")
    
    return dash.no_update # Fallback if triggered_id is not a dict or unexpected


@app.callback(
    [Output("market-quotes-output", "children"),
     Output("quotes-status-output", "children"),
     Output("notification-toast", "is_open", allow_duplicate=True),
     Output("notification-toast", "children", allow_duplicate=True)],
    Input("fetch-quotes-button", "n_clicks"),
    State("quote-symbols-input", "value"),
    prevent_initial_call=True
)
def fetch_live_quotes(n_clicks, symbols_input):
    """
    Fetches and displays live quotes for user-entered symbols using WebSocket data.
    Subscribes to these symbols on the WebSocket.
    """
    if n_clicks:
        # Check if WebSocket is initialized and connected
        if not kitews.kws or not kitews.kws.is_connected():
            return None, html.P("WebSocket not connected. Please ensure you are logged in and the WebSocket is running.", className="text-danger"), True, "WebSocket not connected."
        
        if not symbols_input:
            return None, html.P("Please enter at least one trading symbol.", className="text-warning"), True, "No symbols entered."
        
        symbols_list = [s.strip().upper() for s in symbols_input.split(',') if s.strip()]
        if not symbols_list:
            return None, html.P("No valid symbols entered.", className="text-warning"), True, "No valid symbols."

        tokens_to_fetch = []
        display_quotes = []

        # Get all index tokens to add to the subscription
        all_index_tokens_for_ws = [details["instrument_token"] for details in INDEX_INSTRUMENT_DETAILS.values()]
        tokens_to_fetch.extend(all_index_tokens_for_ws)

        # Map user-entered trading symbols to instrument tokens
        for symbol in symbols_list:
            found_instrument = instrument_df[instrument_df['tradingsymbol'] == symbol]
            if not found_instrument.empty:
                token = found_instrument.iloc[0]['instrument_token']
                tokens_to_fetch.append(token)
            else:
                display_quotes.append({'Symbol': symbol, 'Instrument Token': '-', 'LTP': 'Not Found', 'OI': '-'})

        if not tokens_to_fetch: # This check is now less likely to hit if indices are always added
            return dbc.Table.from_dataframe(pd.DataFrame(display_quotes), striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"), \
                   html.P("No valid instrument tokens found for provided symbols.", className="text-warning"), True, "No valid tokens."

        # Subscribe to these specific symbols for live updates
        kitews.subscribe_to_tokens(list(set(tokens_to_fetch))) # Use set to ensure unique tokens

        # Get the latest quotes from the WebSocket's live_quotes cache
        quotes_from_ws = kitews.get_all_live_quotes()

        # Populate display_quotes with live data for requested symbols (excluding indices if they weren't explicitly requested)
        final_display_symbols = []
        for symbol in symbols_list: # Only display what the user asked for directly in the input box
            found_instrument = instrument_df[instrument_df['tradingsymbol'] == symbol]
            if not found_instrument.empty:
                token = found_instrument.iloc[0]['instrument_token']
                quote_data = quotes_from_ws.get(token, {})
                final_display_symbols.append({
                    'Symbol': symbol,
                    'Instrument Token': token,
                    'LTP': f"{quote_data.get('last_price', '-'):.2f}" if isinstance(quote_data.get('last_price'), (int, float)) else '-',
                    'OI': quote_data.get('oi', '-')
                })
            else:
                final_display_symbols.append({'Symbol': symbol, 'Instrument Token': '-', 'LTP': 'Not Found', 'OI': '-'})


        quotes_df = pd.DataFrame(final_display_symbols)
        table = dbc.Table.from_dataframe(
            quotes_df,
            striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
        )
        return table, html.P("Live LTP quotes fetched successfully from WebSocket.", className="text-success"), True, "LTP quotes fetched successfully."
    
    return None, "", False, "" # Default return

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=8070)
