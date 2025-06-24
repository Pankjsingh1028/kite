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
import json # Import json for ATO basket - This import should resolve 'json' undefined errors.

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
instrument_df = pd.DataFrame() # Stores instruments data
quotes_from_ws = {} # Stores live quotes from WebSocket

# Initialize WebSocket manager
ws_manager = None

# Create a Dash app instance
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG]) # Using a dark theme

# --- Layout ---
app.layout = dbc.Container([
    dcc.Store(id='login-status-store', data={'logged_in': False, 'message': ''}),
    dcc.Store(id='access-token-store', data=SAVED_ACCESS_TOKEN),
    dcc.Store(id='instrument-df-store', data=instrument_df.to_dict('records')), # Store instrument data
    dcc.Store(id='websocket-quote-store', data={}), # Store live quotes

    dbc.Row([
        dbc.Col(html.H1("KiteConnect Dashboard", className="text-center my-4"), width=12)
    ]),

    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Login & Status"),
                dbc.CardBody([
                    dbc.InputGroup([
                        dbc.InputGroupText("API Key"),
                        dbc.Input(id="api-key-input", value=KITE_API_KEY, placeholder="Enter Kite API Key"),
                    ], className="mb-2"),
                    dbc.InputGroup([
                        dbc.InputGroupText("API Secret"),
                        dbc.Input(id="api-secret-input", value=KITE_API_SECRET, placeholder="Enter Kite API Secret", type="password"),
                    ], className="mb-3"),
                    dbc.Button("Generate Login URL", id="generate-login-url-button", color="primary", className="me-2"),
                    dbc.Button("Logout", id="logout-button", color="danger", className="me-2"),
                    dbc.Button("Fetch Instruments", id="fetch-instruments-button", color="info"),
                    html.Div(id="login-url-output", className="mt-3"),
                    html.Div(id="status-message", className="mt-3")
                ])
            ], className="mb-4"),
            md=6
        ),
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("User Profile"),
                dbc.CardBody([
                    html.Div(id="user-profile-output", className="mt-3")
                ])
            ], className="mb-4"),
            md=6
        )
    ]),

    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Instrument Search"),
                dbc.CardBody([
                    dbc.Input(id="instrument-search-input", placeholder="Search instrument (e.g., RELIANCE EQ)", type="text", className="mb-2"),
                    dbc.Button("Search", id="instrument-search-button", color="secondary"),
                    html.Div(id="instrument-search-output", className="mt-3 table-responsive")
                ])
            ], className="mb-4"),
            md=6
        ),
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Live Quotes (LTP & OI)"),
                dbc.CardBody([
                    dbc.Input(id="symbol-input", placeholder="Enter symbols (comma-separated, e.g., RELIANCE,INFY)", type="text", className="mb-2"),
                    dbc.Button("Fetch Live Quotes", id="fetch-quotes-button", color="success"),
                    dbc.Button("Start WebSocket", id="start-websocket-button", color="success", className="ms-2"),
                    dbc.Button("Stop WebSocket", id="stop-websocket-button", color="danger", className="ms-2"),
                    html.Div(id="live-quotes-output", className="mt-3 table-responsive"),
                    html.Div(id="websocket-status", className="mt-3")
                ])
            ], className="mb-4"),
            md=6
        )
    ]),

    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Order Placement / Alert Creation"),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Symbol"), dbc.Input(id="oc-symbol", placeholder="e.g., RELIANCE", type="text")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Exchange"), dbc.Input(id="oc-exchange", placeholder="e.g., NSE", type="text")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Qty"), dbc.Input(id="oc-quantity", value=1, type="number")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Price"), dbc.Input(id="oc-price", placeholder="Limit price (optional)", type="number")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Trigger Price"), dbc.Input(id="oc-trigger-price", placeholder="SL/Alert trigger", type="number")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Product"), dbc.Select(id="oc-product", options=["MIS", "CNC", "NRML"], value="CNC")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Order Type"), dbc.Select(id="oc-order-type", options=["MARKET", "LIMIT", "SL", "SL-M"], value="MARKET")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Validity"), dbc.Select(id="oc-validity", options=["DAY", "IOC"], value="DAY")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Transaction Type"), dbc.Select(id="oc-transaction-type", options=["BUY", "SELL"], value="BUY")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Trade Mode"), dbc.Select(id="oc-trade-mode", options=["ORDER", "GTT", "ALERT"], value="ORDER")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(html.Div(id="alert-specific-options", children=[
                            dbc.InputGroup([dbc.InputGroupText("Alert Trigger Type"), dbc.Select(id="oc-alert-trigger-type", options=["lt", "lte", "gt", "gte"], value="gt")]),
                        ], style={'display': 'none'}), md=12) # Hidden by default
                    ], className="mb-2"),

                    dbc.Button("Execute", id="oc-execute-button", color="primary", className="mt-3"),
                    html.Div(id="oc-status", className="mt-3")
                ])
            ], className="mb-4"),
            md=6
        ),
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Tradebook"),
                dbc.CardBody([
                    dbc.Button("Refresh Tradebook", id="refresh-tradebook-button", color="secondary", className="mb-3"),
                    html.Div(id="tradebook-output", className="table-responsive")
                ])
            ], className="mb-4"),
            md=6
        )
    ]),

    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Holdings"),
                dbc.CardBody([
                    dbc.Button("Refresh Holdings", id="refresh-holdings-button", color="secondary", className="mb-3"),
                    html.Div(id="holdings-output", className="table-responsive")
                ])
            ], className="mb-4"),
            md=6
        ),
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Positions"),
                dbc.CardBody([
                    dbc.Button("Refresh Positions", id="refresh-positions-button", color="secondary", className="mb-3"),
                    html.Div(id="positions-output", className="table-responsive")
                ])
            ], className="mb-4"),
            md=6
        )
    ]),
    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardHeader("Historical Data"),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Symbol"), dbc.Input(id="hist-symbol", placeholder="e.g., RELIANCE", type="text")]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("Interval"), dbc.Select(id="hist-interval", options=["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"], value="day")]), md=6),
                    ], className="mb-2"),
                    dbc.Row([
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("From Date"), dcc.DatePickerSingle(id="hist-from-date", initial_visible_month=datetime.now(), date=datetime.now() - timedelta(days=30))]), md=6),
                        dbc.Col(dbc.InputGroup([dbc.InputGroupText("To Date"), dcc.DatePickerSingle(id="hist-to-date", initial_visible_month=datetime.now(), date=datetime.now())]), md=6),
                    ], className="mb-2"),
                    dbc.Button("Fetch Historical Data", id="fetch-historical-data-button", color="primary", className="mt-3"),
                    html.Div(id="historical-data-output", className="mt-3"),
                    dcc.Graph(id="historical-data-chart", className="mt-3")
                ])
            ], className="mb-4"),
            md=12
        )
    ])

], fluid=True)

# --- Callbacks ---

@app.callback(
    Output('alert-specific-options', 'style'),
    Input('oc-trade-mode', 'value')
)
def toggle_alert_options(trade_mode):
    """
    Toggles the visibility of alert-specific options based on the selected trade mode.
    """
    if trade_mode == 'ALERT':
        return {'display': 'block'}
    return {'display': 'none'}

@app.callback(
    Output('login-url-output', 'children'),
    Output('login-status-store', 'data'),
    Output('api-key-input', 'value'),
    Output('api-secret-input', 'value'),
    Output('access-token-store', 'data', allow_duplicate=True), # Allow duplicate to update access token
    Output('status-message', 'children', allow_duplicate=True),
    Input('generate-login-url-button', 'n_clicks'),
    State('api-key-input', 'value'),
    State('api-secret-input', 'value'),
    prevent_initial_call=True
)
def generate_login_url(n_clicks, api_key, api_secret):
    """
    Generates the KiteConnect login URL and stores API key/secret.
    """
    global kite, KITE_API_KEY, KITE_API_SECRET

    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not api_key or not api_secret:
        return html.P("Please enter both API Key and API Secret.", className="text-danger"), \
               {'logged_in': False, 'message': 'Missing API Key or Secret'}, \
               KITE_API_KEY, KITE_API_SECRET, SAVED_ACCESS_TOKEN, ""

    KITE_API_KEY = api_key
    KITE_API_SECRET = api_secret

    # Save to .env for persistence
    set_key(".env", "KITE_API_KEY", api_key)
    set_key(".env", "KITE_API_SECRET", api_secret)

    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        login_url = kite.login_url()
        webbrowser.open(login_url)
        return html.Div([
            html.P("Login URL generated and opened in browser."),
            html.P(f"Please complete the login at: {login_url}", className="text-info"),
            html.P("After successful login, you will be redirected to http://127.0.0.1:8050/login_response with a request token in the URL. Copy the 'request_token' from the URL and paste it below to generate the access token."),
            dbc.Input(id="request-token-input", placeholder="Paste request_token here", className="mt-2"),
            dbc.Button("Generate Access Token", id="generate-access-token-button", color="success", className="mt-2")
        ]), {'logged_in': False, 'message': 'Login URL generated'}, \
           KITE_API_KEY, KITE_API_SECRET, SAVED_ACCESS_TOKEN, ""
    except Exception as e:
        return html.P(f"Error generating login URL: {e}", className="text-danger"), \
               {'logged_in': False, 'message': f'Error: {e}'}, \
               KITE_API_KEY, KITE_API_SECRET, SAVED_ACCESS_TOKEN, ""

@app.callback(
    Output('status-message', 'children'),
    Output('login-status-store', 'data', allow_duplicate=True),
    Output('access-token-store', 'data'),
    Output('user-profile-output', 'children'),
    Input('generate-access-token-button', 'n_clicks'),
    State('request-token-input', 'value'),
    State('api-key-input', 'value'),
    State('api-secret-input', 'value'),
    prevent_initial_call=True
)
def generate_access_token(n_clicks, request_token, api_key, api_secret):
    """
    Generates the access token using the request token.
    """
    global kite, access_token, user_profile

    if not n_clicks or not request_token:
        raise dash.exceptions.PreventUpdate

    if not api_key or not api_secret:
        return html.P("API Key and Secret are required to generate access token.", className="text-danger"), \
               {'logged_in': False, 'message': 'Missing API Key or Secret'}, \
               dash.no_update, dash.no_update

    try:
        # Re-initialize KiteConnect with API Key if not already done
        if kite is None or kite.api_key != api_key:
            kite = KiteConnect(api_key=api_key)

        data = kite.generate_session(request_token, api_secret)
        access_token = data["access_token"]
        set_key(".env", "KITE_ACCESS_TOKEN", access_token) # Save access token
        user_profile = data["user_id"] # Or fetch full profile if needed kite.profile()
        kite.set_access_token(access_token)

        return html.P("Access Token generated successfully! You are logged in.", className="text-success"), \
               {'logged_in': True, 'message': 'Logged in'}, \
               access_token, \
               html.Div([
                   html.P(f"Welcome, {data.get('user_name', 'User')}!"),
                   html.P(f"Broker: {data.get('broker', 'N/A')}"),
                   html.P(f"User ID: {data.get('user_id', 'N/A')}"),
                   html.P(f"Email: {data.get('email', 'N/A')}")
               ])
    except kc_exceptions.TokenException as e:
        return html.P(f"Token generation failed: {e}. Please ensure the request token is valid and used within 5 minutes.", className="text-danger"), \
               {'logged_in': False, 'message': f'Token error: {e}'}, \
               None, html.Div()
    except Exception as e:
        return html.P(f"An error occurred during token generation: {e}", className="text-danger"), \
               {'logged_in': False, 'message': f'Error: {e}'}, \
               None, html.Div()

@app.callback(
    Output('status-message', 'children', allow_duplicate=True),
    Output('login-status-store', 'data', allow_duplicate=True),
    Output('access-token-store', 'data', allow_duplicate=True),
    Output('user-profile-output', 'children', allow_duplicate=True),
    Input('logout-button', 'n_clicks'),
    prevent_initial_call=True
)
def logout(n_clicks):
    """
    Logs out the user and clears the access token.
    """
    global kite, access_token, user_profile, ws_manager, quotes_from_ws

    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if ws_manager:
        ws_manager.stop_ws()
        ws_manager = None
        quotes_from_ws = {} # Clear quotes on logout

    if kite:
        try:
            kite.invalidate_access_token()
            message = "Logged out successfully."
            status_class = "text-success"
        except Exception as e:
            message = f"Error during logout: {e}"
            status_class = "text-danger"
    else:
        message = "Not logged in."
        status_class = "text-info"

    access_token = None
    user_profile = None
    set_key(".env", "KITE_ACCESS_TOKEN", "") # Clear saved token
    kite = None # Clear kite instance

    return html.P(message, className=status_class), \
           {'logged_in': False, 'message': 'Logged out'}, \
           None, html.Div()

@app.callback(
    Output('status-message', 'children', allow_duplicate=True),
    Output('instrument-df-store', 'data'),
    Input('fetch-instruments-button', 'n_clicks'),
    State('login-status-store', 'data'),
    prevent_initial_call=True
)
def fetch_instruments_data(n_clicks, login_status):
    """
    Fetches instrument data from KiteConnect and stores it.
    """
    global instrument_df

    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to fetch instruments.", className="text-danger"), dash.no_update

    try:
        message_placeholder = html.P("Fetching instruments... This may take a moment.", className="text-info")
        # Initialize an empty DataFrame
        instrument_df = pd.DataFrame()

        # Check if the CSV exists and is recent
        if os.path.exists(INSTRUMENT_CSV_PATH):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(INSTRUMENT_CSV_PATH))
            if datetime.now() - file_mod_time < timedelta(days=7): # Refresh if older than 7 days
                instrument_df = pd.read_csv(INSTRUMENT_CSV_PATH)
                return html.P("Instruments loaded from local CSV (recent).", className="text-success"), instrument_df.to_dict('records')

        # If CSV doesn't exist or is old, fetch from API
        instruments = kite.instruments()
        if instruments:
            instrument_df = pd.DataFrame(instruments)
            instrument_df.to_csv(INSTRUMENT_CSV_PATH, index=False)
            return html.P("Instruments fetched from Kite API and saved to CSV.", className="text-success"), instrument_df.to_dict('records')
        else:
            return html.P("No instruments data received from Kite API.", className="text-warning"), dash.no_update
    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger"), dash.no_update
    except Exception as e:
        return html.P(f"Error fetching instruments: {e}", className="text-danger"), dash.no_update

@app.callback(
    Output('instrument-search-output', 'children'),
    Input('instrument-search-button', 'n_clicks'),
    State('instrument-search-input', 'value'),
    State('instrument-df-store', 'data'),
    prevent_initial_call=True
)
def search_instrument(n_clicks, search_term, instrument_data):
    """
    Searches for instruments in the loaded DataFrame.
    """
    if not n_clicks or not search_term:
        raise dash.exceptions.PreventUpdate

    if not instrument_data:
        return html.P("Please fetch instruments data first.", className="text-warning")

    instrument_df_local = pd.DataFrame(instrument_data)
    search_term_lower = search_term.lower()

    # Search in tradingsymbol, instrument_type, and name
    filtered_df = instrument_df_local[
        (instrument_df_local['tradingsymbol'].str.lower().str.contains(search_term_lower, na=False)) |
        (instrument_df_local['instrument_type'].str.lower().str.contains(search_term_lower, na=False)) |
        (instrument_df_local['name'].str.lower().str.contains(search_term_lower, na=False))
    ]

    if not filtered_df.empty:
        # Select relevant columns for display
        display_df = filtered_df[['instrument_token', 'tradingsymbol', 'exchange', 'instrument_type', 'name', 'last_price']].head(20) # Limit to 20 results for display
        return dbc.Table.from_dataframe(
            display_df,
            striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
        )
    return html.P(f"No instruments found for '{search_term}'.", className="text-warning")

@app.callback(
    Output('live-quotes-output', 'children'),
    Output('status-message', 'children', allow_duplicate=True),
    Input('fetch-quotes-button', 'n_clicks'),
    State('symbol-input', 'value'),
    State('login-status-store', 'data'),
    State('instrument-df-store', 'data'),
    State('websocket-quote-store', 'data'), # Use data from websocket
    prevent_initial_call=True
)
def fetch_live_quotes(n_clicks, symbols, login_status, instrument_data, current_ws_quotes):
    """
    Fetches live LTP and OI for given symbols, preferring WebSocket data if available.
    """
    if not n_clicks or not symbols:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to fetch quotes.", className="text-danger"), ""

    if not instrument_data:
        return html.P("Please fetch instruments data first.", className="text-warning"), ""

    symbols_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    if not symbols_list:
        return html.P("Please enter valid symbols.", className="text-warning"), ""

    instrument_df_local = pd.DataFrame(instrument_data)

    final_display_symbols = []
    # Prioritize WebSocket data for requested symbols (excluding indices if they weren't explicitly requested)
    for symbol in symbols_list: # Only display what the user asked for directly in the input box
        found_instrument = instrument_df_local[instrument_df_local['tradingsymbol'] == symbol]
        if not found_instrument.empty:
            token = found_instrument.iloc[0]['instrument_token']
            quote_data = current_ws_quotes.get(str(token), {}) # Convert token to string for dict key
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
    return table, html.P("Live LTP quotes fetched successfully from WebSocket.", className="text-success")

@app.callback(
    Output('websocket-status', 'children'),
    Output('websocket-quote-store', 'data'), # Update quotes in store
    Input('start-websocket-button', 'n_clicks'),
    Input('stop-websocket-button', 'n_clicks'),
    State('login-status-store', 'data'),
    State('access-token-store', 'data'),
    State('api-key-input', 'value'),
    State('instrument-df-store', 'data'),
    State('symbol-input', 'value'), # Get symbols to subscribe from the input box
    prevent_initial_call=True
)
def manage_websocket(start_n_clicks, stop_n_clicks, login_status, access_token, api_key, instrument_data, symbols_to_subscribe_str):
    """
    Manages the WebSocket connection for live quotes.
    """
    global ws_manager, quotes_from_ws

    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if not login_status['logged_in'] or not access_token or not api_key:
        return html.P("Please log in first to manage WebSocket.", className="text-danger"), dash.no_update

    instrument_df_local = pd.DataFrame(instrument_data) if instrument_data else pd.DataFrame()

    # Get instrument tokens for subscription
    tokens_to_subscribe = []
    if symbols_to_subscribe_str:
        symbols_list = [s.strip().upper() for s in symbols_to_subscribe_str.split(',') if s.strip()]
        for symbol in symbols_list:
            found_instrument = instrument_df_local[instrument_df_local['tradingsymbol'] == symbol]
            if not found_instrument.empty:
                tokens_to_subscribe.append(found_instrument.iloc[0]['instrument_token'])

    if button_id == 'start-websocket-button':
        if ws_manager is None:
            def on_ticks(ws, ticks):
                """Callback to receive ticks."""
                # Update the global quotes_from_ws dictionary
                for tick in ticks:
                    quotes_from_ws[str(tick['instrument_token'])] = tick
                # No direct Dash output update here; it will be triggered by other callbacks
                # that read from quotes_from_ws or websocket-quote-store

            def on_connect(ws, response):
                """Callback on successful connect."""
                if tokens_to_subscribe:
                    ws.subscribe(tokens_to_subscribe)
                    ws.set_mode(ws.MODE_FULL, tokens_to_subscribe)
                print("WebSocket connected and subscribed.")

            def on_close(ws, code, reason):
                """Callback when the WebSocket connection is closed."""
                print(f"WebSocket closed: {code} - {reason}")
                quotes_from_ws = {} # Clear quotes on WS close

            def on_error(ws, code, reason):
                """Callback on WebSocket error."""
                print(f"WebSocket error: {code} - {reason}")


            ws_manager = kitews.KiteWebSocketManager(
                api_key=api_key,
                access_token=access_token,
                on_ticks=on_ticks,
                on_connect=on_connect,
                on_close=on_close,
                on_error=on_error
            )
            ws_manager.start_ws()
            return html.P("WebSocket started.", className="text-success"), quotes_from_ws
        else:
            if tokens_to_subscribe: # If WS already running, just update subscriptions
                ws_manager.ws.subscribe(tokens_to_subscribe)
                ws_manager.ws.set_mode(ws_manager.ws.MODE_FULL, tokens_to_subscribe)
                return html.P("WebSocket already running. Subscribed to new symbols.", className="text-info"), quotes_from_ws
            return html.P("WebSocket already running.", className="text-info"), quotes_from_ws

    elif button_id == 'stop-websocket-button':
        if ws_manager:
            ws_manager.stop_ws()
            ws_manager = None
            quotes_from_ws = {} # Clear quotes on stop
            return html.P("WebSocket stopped.", className="text-danger"), quotes_from_ws
        return html.P("WebSocket is not running.", className="text-info"), dash.no_update

    return dash.no_update, quotes_from_ws # Default case, should not be reached

@app.callback(
    Output('oc-status', 'children'),
    Input('oc-execute-button', 'n_clicks'),
    State('login-status-store', 'data'),
    State('oc-symbol', 'value'),
    State('oc-exchange', 'value'),
    State('oc-quantity', 'value'),
    State('oc-price', 'value'),
    State('oc-trigger-price', 'value'),
    State('oc-product', 'value'),
    State('oc-order-type', 'value'),
    State('oc-validity', 'value'),
    State('oc-transaction-type', 'value'),
    State('oc-trade-mode', 'value'),
    State('oc-alert-trigger-type', 'value'), # For alert specific options
    prevent_initial_call=True
)
def handle_order_or_alert(
    n_clicks, login_status, tradingsymbol, exchange, quantity, price, trigger_price,
    product, order_type, validity, transaction_type, oc_trade_mode, alert_trigger_type
):
    """
    Handles placing an order, GTT, or creating an alert based on the selected mode.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to place orders or create alerts.", className="text-danger")

    # Basic validation
    if not all([tradingsymbol, exchange, quantity, product, order_type, transaction_type]):
        return html.P("Please fill in all required order fields (Symbol, Exchange, Qty, Product, Order Type, Transaction Type).", className="text-danger")

    # Ensure quantity is positive
    if quantity <= 0:
        return html.P("Quantity must be a positive number.", className="text-danger")

    try:
        if oc_trade_mode == 'ORDER':
            # Place a regular order
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=order_type,
                price=price if order_type == kite.ORDER_TYPE_LIMIT else None,
                trigger_price=trigger_price if order_type in [kite.ORDER_TYPE_SL, kite.ORDER_TYPE_SLM] else None,
                validity=validity
            )
            return html.P(f"Order placed successfully! Order ID: {order_id}", className="text-success")

        elif oc_trade_mode == 'GTT':
            # Create a GTT order
            if not trigger_price:
                return html.P("Trigger price is required for GTT orders.", className="text-danger")

            gtt_order_params = {
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": product,
                "order_type": order_type,
                "price": price if order_type == kite.ORDER_TYPE_LIMIT else 0 # Price must be 0 for market, or actual for limit
            }

            gtt_id = kite.place_gtt(
                trigger_type=kite.GTT_TYPE_SINGLE, # Or kite.GTT_TYPE_OCO
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                trigger_values=[trigger_price],
                last_price=0, # This can be 0 or current LTP if available
                orders=[gtt_order_params]
            )
            return html.P(f"GTT order created successfully! GTT ID: {gtt_id}", className="text-success")

        elif oc_trade_mode == 'ALERT':
            # All variables used here (tradingsymbol, exchange, quantity, etc.) are function parameters
            # and are therefore defined when this function is called.
            # Pylance "reportUndefinedVariable" warnings here might be static analysis quirks.
            if not all([alert_trigger_type, trigger_price]):
                return html.P("Alert trigger type and trigger price are required for alerts.", className="text-danger")

            # Construct the order details for the ATO basket
            order_data = {
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": product,
                "order_type": order_type,
                "price": price if order_type == kite.ORDER_TYPE_LIMIT else None, # Only include price for limit orders
                "trigger_price": trigger_price if order_type in [kite.ORDER_TYPE_SL, kite.ORDER_TYPE_SLM] else None,
                "validity": validity,
                # Add any other order parameters relevant to your use case
            }

            # Basket structure as per KiteConnect API documentation for ATO
            basket = {
                "name": f"ATO_{tradingsymbol}_{transaction_type}_{uuid.uuid4().hex[:6]}", # Unique name for the basket
                "type": "regular", # 'regular' for single order within ATO
                "tags": [], # Optional tags
                "items": [order_data]
            }

            basket_json = json.dumps(basket) # 'json' is imported at the top of the file.

            # Call create_alert with type='ato' and the basket JSON
            alert_id = kite.create_alert(
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                trigger_type=alert_trigger_type, # 'lt', 'lte', 'gt', 'gte'
                trigger_value=trigger_price,
                type='ato', # This is crucial for Alert To Order
                basket=basket_json
            )
            return html.P(f"Alert to Order (ATO) created successfully! Alert ID: {alert_id}", className="text-success")

    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger")
    except kc_exceptions.InputException as e:
        return html.P(f"Input error: {e}", className="text-danger")
    except Exception as e:
        return html.P(f"An error occurred: {e}", className="text-danger")

@app.callback(
    Output('tradebook-output', 'children'),
    Input('refresh-tradebook-button', 'n_clicks'),
    State('login-status-store', 'data'),
    prevent_initial_call=True
)
def refresh_tradebook(n_clicks, login_status):
    """
    Refreshes and displays the tradebook.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to refresh tradebook.", className="text-danger")

    try:
        trades = kite.trades()
        if trades:
            trades_df = pd.DataFrame(trades)
            # Select and reorder relevant columns for display
            display_columns = [
                'order_id', 'exchange_order_id', 'tradingsymbol', 'exchange',
                'transaction_type', 'quantity', 'price', 'product', 'order_type',
                'status', 'order_timestamp'
            ]
            # Filter for columns that actually exist in the DataFrame
            existing_columns = [col for col in display_columns if col in trades_df.columns]
            return dbc.Table.from_dataframe(
                trades_df[existing_columns],
                striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
            )
        return html.P("No trades found.", className="text-info")
    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger")
    except Exception as e:
        return html.P(f"Error fetching tradebook: {e}", className="text-danger")

@app.callback(
    Output('holdings-output', 'children'),
    Input('refresh-holdings-button', 'n_clicks'),
    State('login-status-store', 'data'),
    prevent_initial_call=True
)
def refresh_holdings(n_clicks, login_status):
    """
    Refreshes and displays holdings.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to refresh holdings.", className="text-danger")

    try:
        holdings = kite.holdings()
        if holdings:
            holdings_df = pd.DataFrame(holdings)
            return dbc.Table.from_dataframe(
                holdings_df,
                striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
            )
        return html.P("No holdings found.", className="text-info")
    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger")
    except Exception as e:
        return html.P(f"Error fetching holdings: {e}", className="text-danger")

@app.callback(
    Output('positions-output', 'children'),
    Input('refresh-positions-button', 'n_clicks'),
    State('login-status-store', 'data'),
    prevent_initial_call=True
)
def refresh_positions(n_clicks, login_status):
    """
    Refreshes and displays positions (intraday and overnight).
    This function takes 'n_clicks' (from the refresh button) and 'login_status' (from a Dash Store)
    as its input parameters, ensuring they are always defined within its scope.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to refresh positions.", className="text-danger")

    try:
        positions = kite.positions()
        if positions and (positions.get('day') or positions.get('net')):
            all_positions = []
            if positions.get('day'):
                for pos in positions['day']:
                    pos['type'] = 'Day'
                    all_positions.append(pos)
            if positions.get('net'):
                for pos in positions['net']:
                    pos['type'] = 'Net' # Overnight or net positions
                    all_positions.append(pos)

            if all_positions:
                positions_df = pd.DataFrame(all_positions)
                # Select and reorder relevant columns for display
                display_columns = [
                    'tradingsymbol', 'exchange', 'type', 'quantity', 'buy_quantity', 'sell_quantity',
                    'last_price', 'pnl', 'product'
                ]
                # Filter for columns that actually exist in the DataFrame
                existing_columns = [col for col in display_columns if col in positions_df.columns]

                return dbc.Table.from_dataframe(
                    positions_df[existing_columns],
                    striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
                )
        return html.P("No positions found.", className="text-info")
    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger")
    except Exception as e:
        return html.P(f"Error fetching positions: {e}", className="text-danger")

@app.callback(
    Output('historical-data-output', 'children'),
    Output('historical-data-chart', 'figure'),
    Input('fetch-historical-data-button', 'n_clicks'),
    State('login-status-store', 'data'),
    State('instrument-df-store', 'data'),
    State('hist-symbol', 'value'),
    State('hist-interval', 'value'),
    State('hist-from-date', 'date'),
    State('hist-to-date', 'date'),
    prevent_initial_call=True
)
def fetch_historical_data(n_clicks, login_status, instrument_data, symbol, interval, from_date, to_date):
    """
    Fetches and displays historical data, and renders a candlestick chart.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not login_status['logged_in'] or kite is None:
        return html.P("Please log in first to fetch historical data.", className="text-danger"), {}

    if not instrument_data:
        return html.P("Please fetch instruments data first.", className="text-warning"), {}

    if not all([symbol, interval, from_date, to_date]):
        return html.P("Please fill in all historical data fields.", className="text-danger"), {}

    instrument_df_local = pd.DataFrame(instrument_data)
    found_instrument = instrument_df_local[instrument_df_local['tradingsymbol'] == symbol.upper()]

    if found_instrument.empty:
        return html.P(f"Instrument '{symbol}' not found.", className="text-danger"), {}

    instrument_token = found_instrument.iloc[0]['instrument_token']

    try:
        from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
        to_datetime = datetime.strptime(to_date, '%Y-%m-%d')

        data = kite.historical_data(instrument_token, from_datetime, to_datetime, interval)

        if data:
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date']) # Ensure 'date' is datetime object
            df = df.set_index('date')

            # Create candlestick chart
            fig = go.Figure(data=[go.Candlestick(
                x=df.index,
                open=df['open'],
                high=df['high'],
                low=df['low'],
                close=df['close']
            )])

            fig.update_layout(
                title=f'{symbol} Historical Data ({interval})',
                xaxis_title="Date",
                yaxis_title="Price",
                xaxis_rangeslider_visible=False,
                template="plotly_dark" # Use a dark theme for the chart
            )

            return dbc.Table.from_dataframe(
                df.head(), # Displaying only first few rows for brevity
                striped=True, bordered=True, hover=True, responsive=True, className="mt-3 text-center"
            ), fig
        return html.P(f"No historical data found for {symbol} in the given period.", className="text-info"), {}
    except kc_exceptions.TokenException:
        return html.P("Session expired. Please log in again.", className="text-danger"), {}
    except Exception as e:
        return html.P(f"Error fetching historical data: {e}", className="text-danger"), {}

if __name__ == "__main__":
    # This section typically runs the Dash app.
    # In a production environment, you might use a WSGI server like Gunicorn.
    app.run(debug=False, port=8050)
