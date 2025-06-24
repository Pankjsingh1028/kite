import dash
from dash import dcc, html, Input, Output, State, MATCH, ALL
import dash_bootstrap_components as dbc
from kiteconnect import KiteConnect
from kiteconnect import exceptions as kc_exceptions # Import KiteConnect exceptions
import os
import webbrowser
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key, dotenv_values

# Load environment variables from .env file at the very start
load_dotenv()

# --- Configuration ---
KITE_API_KEY = os.getenv("KITE_API_KEY", "YOUR_KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_KITE_API_SECRET")
KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:8050/login_response")
SAVED_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
INSTRUMENT_CSV_PATH = "kite_instruments.csv" # Define a constant for the CSV path

# Global KiteConnect instance and access token storage
kite = None
access_token = SAVED_ACCESS_TOKEN # Initialize with saved token if it exists
user_profile = None
instrument_df = pd.DataFrame() # Initialize as empty DataFrame

# --- Automatic Token Validation and Re-authentication on Startup ---
# This block runs only once when the Python script starts
if SAVED_ACCESS_TOKEN:
    try:
        # Initialize KiteConnect object for startup validation
        temp_kite = KiteConnect(api_key=KITE_API_KEY)
        temp_kite.set_access_token(SAVED_ACCESS_TOKEN)
        
        # Attempt to fetch profile to validate the token
        profile_data = temp_kite.profile()
        user_profile = profile_data.get("user_name")
        kite = temp_kite # If valid, assign to global kite
        print("KiteConnect initialized with valid saved access token.")
    except (kc_exceptions.TokenException, kc_exceptions.PermissionException, Exception) as e:
        # Token is invalid or expired, or another error occurred during profile fetch
        print(f"Saved access token is invalid or expired ({e}). Clearing token and requiring re-authentication.")
        # Clear the invalid token from global variable and .env
        access_token = None
        kite = None
        user_profile = None
        env_path = '.env'
        set_key(env_path, "KITE_ACCESS_TOKEN", "") # Overwrite with empty string to effectively clear it
else:
    print("No saved access token found. User needs to authenticate.")

# --- Load Instrument Master from CSV on Startup if available ---
# This loads instrument data from a local CSV if it exists,
# allowing dropdowns to populate immediately on app start without an API call.
if os.path.exists(INSTRUMENT_CSV_PATH):
    try:
        instrument_df = pd.read_csv(INSTRUMENT_CSV_PATH)
        print(f"Instrument master loaded from {INSTRUMENT_CSV_PATH} on startup. Total instruments: {len(instrument_df)}")
    except Exception as e:
        print(f"Error loading instrument master from CSV on startup: {e}. It will be fetched via API if needed.")


# Initialize the Dash app
app = dash.Dash(__name__, external_stylesheets=[
    dbc.themes.BOOTSTRAP,
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css', # For icons
    '/assets/style.css' # Link to your custom CSS file
])


# --- Dash Layout ---
# Determine initial disabled states based on the global 'access_token' value after startup validation
is_authenticated_on_load = bool(access_token)

app.layout = dbc.Container([
    html.H1("Kite Connect Trading App", className="my-4 text-center"),

    # Row 1: Kite Connect Setup & Authentication Response (Half Proportion)
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Kite Connect Setup"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("API Key:", className="mb-0"),
                        dbc.Input(id="api-key-input", type="text", placeholder="Enter your Kite API Key",
                                  value=KITE_API_KEY),
                    ]), md=12),
                    dbc.Col(html.Div([
                        dbc.Label("API Secret:", className="mb-0"),
                        dbc.Input(id="api-secret-input", type="text", placeholder="Enter your Kite API Secret",
                                  value=KITE_API_SECRET),
                    ]), md=12),
                ], className="g-2 mb-3"),

                html.Div([
                    dbc.Label("Redirect URL (must match Kite app settings):", className="mb-0"),
                    dbc.Input(id="redirect-url-input", type="text", placeholder="e.g., http://127.0.0.1:8050/login_response",
                              value=KITE_REDIRECT_URL, className="mb-3"),
                ]),
                dbc.Row([
                    dbc.Col(dbc.Button("Initialize Kite & Get Login URL", id="init-kite-button", color="primary", className="me-2",
                                       disabled=is_authenticated_on_load), width="auto"), # Enabled if not authenticated
                    dbc.Col(dbc.Button("Open Kite Login Page", id="open-login-button", color="info", className="ms-2",
                                       disabled=is_authenticated_on_load), width="auto"), # Enabled if not authenticated
                    dbc.Col(dbc.Button("Clear Saved Token", id="clear-token-button", color="warning", className="ms-2",
                                       disabled=not is_authenticated_on_load), width="auto"), # Enabled if authenticated
                ], className="g-2 justify-content-start"),
                html.Div(id="login-url-output", className="mt-3"),
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=6),

        dbc.Col(dbc.Card([
            dbc.CardHeader("Authentication Response"),
            dbc.CardBody([
                html.Div([
                    dbc.Label("Request Token (from Kite redirect URL):", className="mb-0"),
                    dbc.Input(id="request-token-input", type="text", placeholder="Paste request_token here", className="mb-3"),
                ]),
                dbc.Button("Generate Access Token", id="generate-token-button", color="success",
                           disabled=is_authenticated_on_load), # Enabled if not authenticated
                html.Div(id="access-token-output", className="mt-3"), # Initial message will be set by callback
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=6),
    ], className="mb-4 g-3"),

    # Row 2: Trading Dashboard (1/3) & Instrument Master & Option Chain (2/3)
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Trading Dashboard"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Button("Fetch User Profile", id="fetch-profile-button", color="secondary", className="me-2",
                                       disabled=not is_authenticated_on_load), width="auto"), # Enabled if authenticated
                    dbc.Col(dbc.Button("Fetch Holdings (Mock)", id="fetch-holdings-button", color="secondary",
                                       disabled=not is_authenticated_on_load), width="auto"), # Enabled if authenticated
                ], className="g-2 mb-3 justify-content-start"),
                html.Hr(),
                html.Div(id="profile-output"),
                html.Div(id="holdings-output", className="mt-3"),

                html.H4("Place Order", className="mt-4"),
                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Instrument (e.g., RELIANCE)", className="mb-0"),
                        dbc.Input(id="instrument-input", type="text"),
                    ]), md=4),
                    dbc.Col(html.Div([
                        dbc.Label("Quantity", className="mb-0"),
                        dbc.Input(id="quantity-input", type="number"),
                    ]), md=4),
                    dbc.Col(html.Div([
                        dbc.Label("Price (Optional)", className="mb-0"),
                        dbc.Input(id="price-input", type="number"),
                    ]), md=4),
                ], className="g-2 mb-3"),
                dbc.Row([
                    dbc.Col(html.Div([
                        dbc.Label("Transaction Type", className="mb-0"),
                        dbc.Select(
                            id="transaction-type-select",
                            options=[
                                {"label": "BUY", "value": "BUY"},
                                {"label": "SELL", "value": "SELL"}
                            ],
                            value="BUY",
                        ),
                    ]), md=6),
                    dbc.Col(html.Div([
                        dbc.Label("Order Type", className="mb-0"),
                        dbc.Select(
                            id="order-type-select",
                            options=[
                                {"label": "MARKET", "value": "MARKET"},
                                {"label": "LIMIT", "value": "LIMIT"}
                            ],
                            value="MARKET",
                        ),
                    ]), md=6),
                ], className="g-2 mb-3"),
                dbc.Button("Place Order", id="place-order-button", color="danger",
                           disabled=not is_authenticated_on_load), # Enabled if authenticated
                html.Div(id="order-output", className="mt-3"),
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=4),

        dbc.Col(dbc.Card([
            dbc.CardHeader("Instrument Master & Option Chain"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Button("Load & Save Instrument Data", id="load-save-instruments-button", color="info", className="me-2",
                                       disabled=not is_authenticated_on_load), width="auto"), # Enabled if authenticated
                ], className="g-2 mb-3 justify-content-start"),
                dcc.Loading(
                    id="loading-instruments",
                    type="circle",
                    children=html.Div(id="instrument-status-output")
                ),
                html.Div(id="csv-status-output", className="mt-2"),
                html.Hr(),
                html.H5("Option Chain Table", className="mb-2"),
                
                # New section for configurable order parameters
                html.Div([
                    html.H6("Configurable Order Parameters for Option Chain Actions:", className="mt-4 mb-2"),
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
                                    {"label": "CNC (Delivery/Equity)", "value": "CNC"},
                                    {"label": "NRML (Carry Forward/F&O)", "value": "NRML"}
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
                            value="NIFTY", # Default selected index
                        ),
                    ]), md=6),
                    dbc.Col(html.Div([
                        dbc.Label("Select Expiry Date:", className="mb-0"),
                        dbc.Select(
                            id='option-expiry-select',
                            placeholder="Select an Expiry Date",
                            options=[], # Populated by callback
                            value=None, # Will be set by callback
                        ),
                    ]), md=6),
                ], className="g-2 mb-3"),
                dbc.Button("Generate Option Chain Table", id="plot-option-chain-button", color="success",
                           disabled=not is_authenticated_on_load, className="mt-0"), # Enabled if authenticated
                dcc.Loading(
                    id="loading-option-chain",
                    type="circle",
                    children=html.Div(id="option-chain-status-output")
                ),
                html.Div(id="option-chain-table", className="mt-3"),
                html.Div(id="oc-order-output", className="mt-3") # New output for option chain order messages
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=8),
    ], className="mb-4 g-3"),

    # Row 3: Live Market Quotes (LTP)
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Live Market Quotes (LTP)"),
            dbc.CardBody([
                html.Div([
                    dbc.Label("Enter Trading Symbols (comma-separated, e.g., NSE:RELIANCE,NFO:BANKNIFTY25JUN45000CE)", className="mb-0"),
                    dbc.Input(id="quote-symbols-input", type="text", placeholder="NSE:INFY,NFO:NIFTY25JUN19500CE", className="mb-3"),
                ]),
                dbc.Button("Fetch Live LTP", id="fetch-quotes-button", color="primary",
                           disabled=not is_authenticated_on_load, className="mt-0"), # Enabled if authenticated
                dcc.Loading(
                    id="loading-quotes",
                    type="circle",
                    children=html.Div(id="quotes-status-output")
                ),
                html.Div(id="market-quotes-output", className="mt-3")
            ], className="p-3")
        ], className="shadow-sm rounded-lg h-100"), md=12),
    ], className="mb-4 g-3"),

], fluid=True, style={"fontFamily": "Inter, sans-serif", "margin": "auto"})


# --- Dash Callbacks ---

@app.callback(
    Output("login-url-output", "children"),
    Output("open-login-button", "disabled", allow_duplicate=True),
    Output("init-kite-button", "disabled", allow_duplicate=True),
    Output("clear-token-button", "disabled", allow_duplicate=True),
    Input("init-kite-button", "n_clicks"),
    Input("clear-token-button", "n_clicks"),
    State("api-key-input", "value"),
    State("api-secret-input", "value"),
    State("redirect-url-input", "value"),
    prevent_initial_call=True
)
def handle_kite_init_and_clear(init_n_clicks, clear_n_clicks, api_key, api_secret, redirect_url):
    """Initializes KiteConnect and handles clearing the saved token."""
    global kite, access_token, user_profile

    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if button_id == "clear-token-button" and clear_n_clicks:
        env_path = '.env'
        env_values = dotenv_values(env_path)
        if "KITE_ACCESS_TOKEN" in env_values:
            del env_values["KITE_ACCESS_TOKEN"]
            set_key(env_path, "KITE_ACCESS_TOKEN", "")

        access_token = None
        kite = None
        user_profile = None
        global instrument_df
        instrument_df = pd.DataFrame() # Clear instruments on logout
        
        # After clearing, re-enable init/open login and disable clear token
        return html.P("Saved token cleared. Please re-authenticate.", className="text-info"), \
               False, False, True # open-login-button, init-kite-button (enabled), clear-token-button (disabled)

    elif button_id == "init-kite-button" and init_n_clicks:
        try:
            global KITE_API_KEY, KITE_API_SECRET, KITE_REDIRECT_URL
            KITE_API_KEY = api_key
            KITE_API_SECRET = api_secret
            KITE_REDIRECT_URL = redirect_url

            kite = KiteConnect(api_key=KITE_API_KEY)
            login_url = kite.login_url()
            return [
                html.P(f"Kite Connect initialized. Login URL generated:"),
                html.A(login_url, href=login_url, target="_blank", rel="noopener noreferrer")
            ], False, True, False # open-login-button (enabled), init-kite-button (disabled), clear-token-button (disabled)
        except Exception as e:
            return html.P(f"Error initializing Kite: {e}", className="text-danger"), True, False, True
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


@app.callback(
    Output("open-login-button", "n_clicks"),
    Input("open-login-button", "n_clicks"),
    State("login-url-output", "children"),
    prevent_initial_call=True
)
def open_login_page(n_clicks, login_url_element):
    """Opens the Kite login URL in a new browser tab."""
    if n_clicks and login_url_element:
        if isinstance(login_url_element, list) and len(login_url_element) > 1 and hasattr(login_url_element[1], 'href'):
            url = login_url_element[1].href
            webbrowser.open_new_tab(url)
    return dash.no_update


@app.callback(
    Output("access-token-output", "children"),
    Output("fetch-profile-button", "disabled", allow_duplicate=True),
    Output("fetch-holdings-button", "disabled", allow_duplicate=True),
    Output("place-order-button", "disabled", allow_duplicate=True),
    Output("load-save-instruments-button", "disabled", allow_duplicate=True), # Updated ID
    Output("plot-option-chain-button", "disabled", allow_duplicate=True),
    Output("fetch-quotes-button", "disabled", allow_duplicate=True),
    Output("init-kite-button", "disabled", allow_duplicate=True),
    Output("open-login-button", "disabled", allow_duplicate=True),
    Output("clear-token-button", "disabled", allow_duplicate=True),
    Output("generate-token-button", "disabled", allow_duplicate=True), # Control this button's state from here
    Input("generate-token-button", "n_clicks"),
    State("request-token-input", "value"),
    State("api-secret-input", "value"),
    prevent_initial_call=True
)
def generate_access_token(n_clicks, request_token, api_secret):
    """Generates the access token using the request token and saves it."""
    global access_token, user_profile, kite
    if n_clicks and request_token and kite:
        try:
            data = kite.generate_session(request_token, api_secret=api_secret)
            access_token = data["access_token"]
            user_profile = data["user_name"]

            kite.set_access_token(access_token)

            env_path = '.env'
            set_key(env_path, "KITE_ACCESS_TOKEN", access_token)
            print(f"KITE_ACCESS_TOKEN saved to {env_path}")

            # Return 11 values: 1 for access-token-output and 10 for disabled states
            return html.Div([
                html.P("Access Token generated and saved successfully!"),
                html.P(f"User: {user_profile}"),
                html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
            ], className="text-success"), \
            False, False, False, False, False, False, \
            True, True, False, True # All action buttons enabled, init/open login disabled, clear token enabled, generate token disabled
        except Exception as e:
            access_token = None
            user_profile = None
            # Return 11 values: 1 for access-token-output and 10 for disabled states
            return html.P(f"Error generating access token: {e}", className="text-danger"), \
                   True, True, True, True, True, True, \
                   False, False, True, False # All action buttons disabled, init/open login enabled, clear token disabled, generate token enabled (as it failed)
    # Default return if no clicks or missing data
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update, \
           dash.no_update, dash.no_update, dash.no_update, dash.no_update, \
           dash.no_update, dash.no_update, dash.no_update


# This callback is responsible for setting the initial button states on app load
# It also reacts to changes in access_token (which can be set by generate_access_token or on startup validation)
@app.callback(
    Output("fetch-profile-button", "disabled", allow_duplicate=True),
    Output("fetch-holdings-button", "disabled", allow_duplicate=True),
    Output("place-order-button", "disabled", allow_duplicate=True),
    Output("load-save-instruments-button", "disabled", allow_duplicate=True), # Updated ID
    Output("plot-option-chain-button", "disabled", allow_duplicate=True),
    Output("fetch-quotes-button", "disabled", allow_duplicate=True),
    Output("init-kite-button", "disabled", allow_duplicate=True),
    Output("open-login-button", "disabled", allow_duplicate=True),
    Output("clear-token-button", "disabled", allow_duplicate=True),
    Output("generate-token-button", "disabled", allow_duplicate=True), # Control this button's state from here
    Output("access-token-output", "children", allow_duplicate=True), # Also control access token message here
    Input('access-token-output', 'children'), # Trigger on app load and when access-token-output changes
    prevent_initial_call='callback_args_grouping'
)
def update_button_states_on_load_or_token_change(access_token_output_children_input_value):
    """Sets initial and dynamic button states and access token output message based on authentication status."""
    is_authenticated = bool(access_token) # Check global access_token status

    # Determine message for access-token-output
    display_message = ""
    if is_authenticated and user_profile:
        display_message = html.Div([
            html.P("Loaded with saved Access Token!"),
            html.P(f"User: {user_profile}"),
            html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
        ], className="text-success")
    elif is_authenticated: # Should not typically hit here if profile fetch works on startup
        display_message = html.Div([
            html.P("Loaded with saved Access Token (profile not fetched)!"),
            html.P(f"Public Token (last 4 chars): {access_token[-4:]}...")
        ], className="text-success")
    else:
        display_message = html.P("No valid access token found. Please initialize Kite and authenticate.", className="text-info")

    return (
        not is_authenticated, # Fetch User Profile (disabled if not authenticated)
        not is_authenticated, # Fetch Holdings (disabled if not authenticated)
        not is_authenticated, # Place Order (disabled if not authenticated)
        not is_authenticated, # Load Instruments (disabled if not authenticated)
        not is_authenticated, # Plot Option Chain (disabled if not authenticated)
        not is_authenticated, # Fetch Live LTP (disabled if not authenticated)
        is_authenticated,     # Initialize Kite & Get Login URL (enabled if NOT auth)
        is_authenticated,     # Open Kite Login Page (enabled if NOT auth)
        not is_authenticated, # Clear Saved Token (enabled if IS auth)
        is_authenticated,     # Generate Access Token (disabled if IS auth)
        display_message       # access-token-output children
    )


@app.callback(
    Output("profile-output", "children"),
    Input("fetch-profile-button", "n_clicks"),
    prevent_initial_call=True
)
def fetch_user_profile(n_clicks):
    """Fetches and displays the user profile using KiteConnect."""
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
    """Mocks fetching holdings. Replace with actual Kite API call."""
    if n_clicks and kite and access_token:
        try:
            mock_holdings = [
                {"instrument": "RELIANCE", "quantity": 10, "avg_cost": 2500.50},
                {"instrument": "TCS", "quantity": 5, "avg_cost": 3800.25},
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
    Output("instrument-status-output", "children"),
    Output("csv-status-output", "children"), # Now only one output for status
    Input("load-save-instruments-button", "n_clicks"), # New single button
    prevent_initial_call=True
)
def load_and_save_instrument_master(n_clicks):
    """Loads the instrument master dump from Kite and saves it to a CSV file."""
    global instrument_df
    if n_clicks and kite and access_token:
        try:
            status_message = ""
            csv_message = ""

            # Fetch instruments from Kite API
            instruments = kite.instruments()
            instrument_df = pd.DataFrame(instruments)
            status_message = html.P(f"Instrument master loaded from Kite API. Total instruments: {len(instrument_df)}", className="text-success")

            # Save to CSV
            if not instrument_df.empty:
                try:
                    instrument_df.to_csv(INSTRUMENT_CSV_PATH, index=False)
                    csv_message = html.P(f"Instrument data saved to {INSTRUMENT_CSV_PATH} successfully!", className="text-success")
                except Exception as e:
                    csv_message = html.P(f"Error saving instruments to CSV: {e}", className="text-danger")
            else:
                csv_message = html.P("No instrument data to save to CSV.", className="text-warning")

            return status_message, csv_message

        except Exception as e:
            instrument_df = pd.DataFrame() # Clear df if API fetch fails
            status_message = html.P(f"Error loading instrument master from Kite API: {e}. Please ensure you are logged in and API is accessible.", className="text-danger")
            csv_message = html.P("Failed to load instrument data, so no CSV was saved/updated.", className="text-warning")
            return status_message, csv_message
    
    return "", "" # Default empty state if not triggered or not authenticated

# Callback to enable/disable price input based on order type selection
@app.callback(
    Output('oc-price-input', 'disabled'),
    Input('oc-order-type-select', 'value'),
    prevent_initial_call=False
)
def toggle_oc_price_input(order_type):
    return order_type == 'MARKET'


# Callback to toggle visibility of GTT and Alert inputs
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

# Callback to toggle visibility of single-leg vs two-leg GTT inputs
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


# New callback to dynamically populate expiry dates based on selected index
@app.callback(
    Output('option-expiry-select', 'options'),
    Output('option-expiry-select', 'value'),
    Input('index-select', 'value'),
    Input('load-save-instruments-button', 'n_clicks'), # Trigger when instruments are (re)loaded/saved
    prevent_initial_call=False # This callback should run on initial load to set default expiry
)
def update_expiry_dropdown(selected_index, load_save_instruments_n_clicks):
    # This callback will run on initial load.
    # It will also run if the index-select value changes, or if the load-save-instruments-button is clicked.
    
    if selected_index and not instrument_df.empty:
        # Filter for options of the selected index
        options_for_index = instrument_df[
            (instrument_df['instrument_type'].isin(['CE', 'PE'])) &
            (instrument_df['name'] == selected_index.upper()) &
            (instrument_df['exchange'] == 'NFO') # Assuming NFO for options
        ].copy()

        if options_for_index.empty:
            return [], None # No options found for this index

        # Safely convert 'expiry' column to datetime.date objects for this callback
        # errors='coerce' will turn unparseable dates into NaT (Not a Time)
        # .dt.date extracts just the date part as datetime.date objects (if successful)
        parsed_expiries = pd.to_datetime(options_for_index['expiry'], errors='coerce').dt.date
        unique_expiries_dt = parsed_expiries.dropna().unique() # Get unique valid dates

        valid_expiries = sorted(unique_expiries_dt) # Sort the datetime.date objects

        # Filter out past expiry dates
        current_date = datetime.now().date()
        valid_expiries = [dt for dt in valid_expiries if dt >= current_date]


        # Format for dropdown options
        expiry_options = [{"label": dt.strftime('%d-%m-%Y'), "value": dt.strftime('%Y-%m-%d')} for dt in valid_expiries]

        # Set default value to the first available expiry, or None if no expiries
        default_expiry_value = expiry_options[0]['value'] if expiry_options else None
        return expiry_options, default_expiry_value
    
    # If instrument_df is empty or no index selected, clear dropdown
    return [], None 


@app.callback(
    Output("option-chain-table", "children"),
    Output("option-chain-status-output", "children"),
    Input("plot-option-chain-button", "n_clicks"),
    State("index-select", "value"), # Changed from option-instrument-input
    State("option-expiry-select", "value"), # Changed from option-expiry-date-picker
    prevent_initial_call=True
)
def plot_option_chain(n_clicks, underlying_symbol, expiry_date_str):
    """Generates the option chain table for the given underlying and expiry, with ITM highlighting."""
    if n_clicks and kite and access_token and not instrument_df.empty:
        try:
            if not underlying_symbol or not expiry_date_str:
                return None, html.P("Please select an underlying index and an expiry date.", className="text-warning")

            expiry_date_dt = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()

            # --- Fetch Underlying LTP for ITM Highlighting ---
            underlying_instrument = instrument_df[
                (instrument_df['tradingsymbol'] == underlying_symbol.upper()) &
                (instrument_df['segment'].isin(['EQ', 'IND'])) # Allow both equity and index segments
            ]
            current_ltp = None
            if not underlying_instrument.empty:
                # Prioritize 'IND' segment if multiple are found for the same symbol (e.g., NIFTY might have EQ and IND)
                index_token_row = underlying_instrument[underlying_instrument['segment'] == 'IND'].iloc[0] if 'IND' in underlying_instrument['segment'].values else underlying_instrument.iloc[0]
                underlying_token = f"{index_token_row['exchange']}:{index_token_row['tradingsymbol']}"
                try:
                    ltp_data = kite.ltp([underlying_token])
                    if ltp_data and underlying_token in ltp_data:
                        current_ltp = ltp_data[underlying_token]['last_price']
                        print(f"Underlying {underlying_symbol} LTP: {current_ltp}")
                except Exception as ltp_e:
                    print(f"Could not fetch LTP for {underlying_symbol}: {ltp_e}")
                    current_ltp = None # Ensure it's None if fetching fails


            filtered_options = instrument_df[
                (instrument_df['instrument_type'].isin(['CE', 'PE'])) &
                (instrument_df['name'] == underlying_symbol.upper()) &
                (instrument_df['exchange'] == 'NFO')
            ].copy()

            filtered_options['expiry_dt'] = pd.to_datetime(filtered_options['expiry'], errors='coerce').dt.date

            options_for_expiry = filtered_options[filtered_options['expiry_dt'] == expiry_date_dt]

            if options_for_expiry.empty:
                return None, html.P(f"No options found for {underlying_symbol.upper()} with expiry {expiry_date_str}", className="text-warning")

            calls = options_for_expiry[options_for_expiry['instrument_type'] == 'CE'].sort_values(by='strike')
            puts = options_for_expiry[puts['instrument_type'] == 'PE'].sort_values(by='strike')

            instrument_tokens = list(calls['instrument_token']) + list(puts['instrument_token'])
            symbols_to_quote = []
            for token in instrument_tokens:
                matching_instrument = instrument_df[instrument_df['instrument_token'] == token]
                if not matching_instrument.empty:
                    row = matching_instrument.iloc[0]
                    symbols_to_quote.append(f"{row['exchange']}:{row['tradingsymbol']}")

            quotes = {}
            chunk_size = 200
            for i in range(0, len(symbols_to_quote), chunk_size):
                chunk_symbols = symbols_to_quote[i:i + chunk_size]
                try:
                    chunk_quotes = kite.quote(chunk_symbols) # Use full quote for OI, Volume, Depth
                    quotes.update(chunk_quotes)
                except Exception as quote_e:
                    print(f"Error fetching quotes for chunk: {quote_e}")
                    continue

            # Building the HTML table directly for granular control
            header_row_1 = html.Tr([
                html.Th(html.Div("CALLS", className="text-center"), colSpan=6, className="call-col"),
                html.Th(html.Div("Strike", className="text-center"), rowSpan=2, className="strike-col"),
                html.Th(html.Div("PUTS", className="text-center"), colSpan=6, className="put-col"),
            ])

            header_row_2 = html.Tr([
                html.Th("OI"),
                html.Th("Vol"),
                html.Th("Bid"),
                html.Th("Ask"),
                html.Th("LTP"),
                html.Th("Act"), # Action column for BUY/SELL
                html.Th("LTP"),
                html.Th("Bid"),
                html.Th("Ask"),
                html.Th("Vol"),
                html.Th("OI"),
                html.Th("Act"), # Action column for BUY/SELL
            ])

            table_body_rows = []
            strikes = sorted(list(options_for_expiry['strike'].unique()))

            for strike in strikes:
                call_option = calls[calls['strike'] == strike].iloc[0] if not calls[calls['strike'] == strike].empty else {}
                put_option = puts[puts['strike'] == strike].iloc[0] if not puts[puts['strike'] == strike].empty else {}

                symbol_key_ce = f"{call_option.get('exchange', 'NFO')}:{call_option.get('tradingsymbol', '')}"
                quote_ce = quotes.get(symbol_key_ce, {})

                symbol_key_pe = f"{put_option.get('exchange', 'NFO')}:{put_option.get('tradingsymbol', '')}"
                quote_pe = quotes.get(symbol_key_pe, {})

                # Determine ITM classes
                call_itm_class = "itm-call" if current_ltp is not None and strike < current_ltp else ""
                put_itm_class = "itm-put" if current_ltp is not None and strike > current_ltp else ""


                row_cells = [
                    html.Td(quote_ce.get('oi', '-'), className=call_itm_class),
                    html.Td(quote_ce.get('volume', '-'), className=call_itm_class),
                    html.Td(quote_ce.get('depth', {}).get('buy', [{}])[0].get('price', '-'), className=call_itm_class),
                    html.Td(quote_ce.get('depth', {}).get('sell', [{}])[0].get('price', '-'), className=call_itm_class),
                    html.Td(f"{quote_ce.get('last_price', '-'):.2f}" if isinstance(quote_ce.get('last_price'), (int, float)) else '-', className=call_itm_class),
                    html.Td([
                        # Buy button for Calls
                        dbc.Button("B", id={
                            'type': 'oc-order-button',
                            'action': 'BUY',
                            'tradingsymbol': str(call_option.get('tradingsymbol')), # Convert to string
                            'exchange': str(call_option.get('exchange')), # Convert to string
                            'lot_size': str(call_option.get('lot_size')), # Convert to string
                            'last_price': str(quote_ce.get('last_price')), # Convert to string
                            'strike': str(call_option.get('strike')), # Convert to string
                            'instrument_type': str(call_option.get('instrument_type')) # Convert to string
                        }, color="success", size="sm", className="btn-oc me-1",
                        disabled=not bool(access_token) # Disable if not authenticated
                        ),
                        # Sell button for Calls
                        dbc.Button("S", id={
                            'type': 'oc-order-button',
                            'action': 'SELL',
                            'tradingsymbol': str(call_option.get('tradingsymbol')), # Convert to string
                            'exchange': str(call_option.get('exchange')), # Convert to string
                            'lot_size': str(call_option.get('lot_size')), # Convert to string
                            'last_price': str(quote_ce.get('last_price')), # Convert to string
                            'strike': str(call_option.get('strike')), # Convert to string
                            'instrument_type': str(call_option.get('instrument_type')) # Convert to string
                        }, color="danger", size="sm", className="btn-oc",
                        disabled=not bool(access_token) # Disable if not authenticated
                        )
                    ], className=call_itm_class),
                    html.Td(f"{strike:.2f}", className="strike-col"),
                    html.Td(f"{quote_pe.get('last_price', '-'):.2f}" if isinstance(quote_pe.get('last_price'), (int, float)) else '-', className=put_itm_class),
                    html.Td(quote_pe.get('depth', {}).get('buy', [{}])[0].get('price', '-'), className=put_itm_class),
                    html.Td(quote_pe.get('depth', {}).get('sell', [{}])[0].get('price', '-'), className=put_itm_class),
                    html.Td(quote_pe.get('volume', '-'), className=put_itm_class),
                    html.Td(quote_pe.get('oi', '-'), className=put_itm_class),
                    html.Td([
                        # Buy button for Puts
                        dbc.Button("B", id={
                            'type': 'oc-order-button',
                            'action': 'BUY',
                            'tradingsymbol': str(put_option.get('tradingsymbol')), # Convert to string
                            'exchange': str(put_option.get('exchange')), # Convert to string
                            'lot_size': str(put_option.get('lot_size')), # Convert to string
                            'last_price': str(quote_pe.get('last_price')), # Convert to string
                            'strike': str(put_option.get('strike')), # Convert to string
                            'instrument_type': str(put_option.get('instrument_type')) # Convert to string
                        }, color="success", size="sm", className="btn-oc me-1",
                        disabled=not bool(access_token) # Disable if not authenticated
                        ),
                        # Sell button for Puts
                        dbc.Button("S", id={
                            'type': 'oc-order-button',
                            'action': 'SELL',
                            'tradingsymbol': str(put_option.get('tradingsymbol')), # Convert to string
                            'exchange': str(put_option.get('exchange')), # Convert to string
                            'lot_size': str(put_option.get('lot_size')), # Convert to string
                            'last_price': str(quote_pe.get('last_price')), # Convert to string
                            'strike': str(put_option.get('strike')), # Convert to string
                            'instrument_type': str(put_option.get('instrument_type')) # Convert to string
                        }, color="danger", size="sm", className="btn-oc",
                        disabled=not bool(access_token) # Disable if not authenticated
                        )
                    ], className=put_itm_class),
                ]
                table_body_rows.append(html.Tr(row_cells))

            oc_table = dbc.Table(
                [html.Thead([header_row_1, header_row_2]), html.Tbody(table_body_rows)],
                bordered=True,
                responsive=True,
                className="oc-table mt-3" # Apply custom class
            )

            return oc_table, html.P("Option chain table generated successfully.", className="text-success")

        except Exception as e:
            return None, html.P(f"Error generating option chain table: {e}", className="text-danger")
    elif n_clicks and (instrument_df.empty or not access_token):
        return None, html.P("Please load instrument master and ensure you are logged in.", className="text-warning")
    return None, ""

# New callback to handle clicks on option chain order buttons
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
                        gtt_type=kite.GTT_TYPE_SINGLE, # Correct GTT type parameter
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
                        gtt_type=kite.GTT_TYPE_OCO, # Correct GTT type parameter for OCO
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
    Output("market-quotes-output", "children"),
    Output("quotes-status-output", "children", allow_duplicate=True),
    Input("fetch-quotes-button", "n_clicks"),
    State("quote-symbols-input", "value"),
    prevent_initial_call=True
)
def fetch_live_quotes(n_clicks, symbols_input):
    """Fetches and displays live LTP quotes for specified symbols."""
    if n_clicks and kite and access_token:
        if not symbols_input:
            return None, html.P("Please enter at least one trading symbol.", className="text-warning")

        symbols_list = [s.strip().upper() for s in symbols_input.split(',') if s.strip()]

        if not symbols_list:
            return None, html.P("No valid symbols entered.", className="text-warning")

        formatted_symbols = []
        for symbol in symbols_list:
            if ':' not in symbol:
                formatted_symbols.append(f"NSE:{symbol}") # Default to NSE for equities
            else:
                formatted_symbols.append(symbol)

        try:
            ltp_quotes = kite.ltp(formatted_symbols)

            if not ltp_quotes:
                return None, html.P("No LTP quotes found for the given symbols. Check if symbols are correct and market is open.", className="text-warning")

            quote_data_for_table = []
            for symbol_key, data in ltp_quotes.items():
                quote_data = {
                    'Symbol': symbol_key,
                    'Instrument Token': data.get('instrument_token', '-'),
                    'LTP': data.get('last_price', '-'),
                }
                quote_data_for_table.append(quote_data)

            quotes_df = pd.DataFrame(quote_data_for_table)

            table = dbc.Table.from_dataframe(
                quotes_df,
                striped=True,
                bordered=True,
                hover=True,
                responsive=True,
                className="mt-3 text-center"
            )

            return table, html.P("Live LTP quotes fetched successfully.", className="text-success")

        except Exception as e:
            return None, html.P(f"Error fetching LTP quotes: {e}", className="text-danger")
    return None, ""


if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=8070)

