import logging
from kiteconnect import KiteTicker
import threading
import time
import signal
import sys # Import sys to check platform for signal handling workaround

# Set up logging for better debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global dictionary to store live quotes (instrument_token: tick_data)
# This dictionary will be updated by the on_ticks callback and accessed by kite2.py
live_quotes = {}

# Global KiteTicker instance and authentication details
kws = None
access_token_ws = None
api_key_ws = None
always_subscribed_tokens = set() # New: Set to hold tokens that should always be subscribed

def on_ticks(ws, ticks):
    """
    Callback to receive ticks.
    Processes incoming tick data and updates the global live_quotes dictionary.
    """
    for tick in ticks:
        instrument_token = tick.get('instrument_token')
        if instrument_token:
            # Store key details for the instrument
            live_quotes[instrument_token] = {
                'instrument_token': instrument_token,
                'last_price': tick.get('last_price'),
                'ohlc': tick.get('ohlc'),
                'volume': tick.get('volume_traded'),
                'oi': tick.get('oi'),
                'depth': tick.get('depth'),
                'timestamp': time.time() # Add a timestamp for freshness (optional)
            }
    # logging.debug(f"Received {len(ticks)} ticks. live_quotes size: {len(live_quotes)}")

def on_connect(ws, response):
    """
    Callback on successful WebSocket connection.
    Logs successful connection and subscribes to always_subscribed_tokens.
    """
    logging.info("Kite WebSocket connected.")
    # On connect, subscribe to all tokens that should always be active
    if always_subscribed_tokens:
        logging.info(f"Subscribing to {len(always_subscribed_tokens)} initial instruments on connect.")
        ws.subscribe(list(always_subscribed_tokens))
        ws.set_mode(ws.MODE_FULL, list(always_subscribed_tokens))

def on_close(ws, code, reason):
    """
    Callback when the WebSocket connection is closed.
    Logs the reason for closure.
    """
    logging.warning(f"Kite WebSocket closed - Code: {code}, Reason: {reason}")

def on_error(ws, code, reason):
    """
    Callback for WebSocket errors.
    Logs the error details.
    """
    logging.error(f"Kite WebSocket error - Code: {code}, Reason: {reason}")

def on_reconnect(ws, attempt_count):
    """
    Callback for reconnection attempts.
    Logs the attempt number.
    """
    logging.info(f"Kite WebSocket reconnecting - Attempt: {attempt_count}")

def on_noreconnect(ws):
    """
    When reconnect failed completely.
    Indicates a permanent loss of connection.
    """
    logging.error("Kite WebSocket: No reconnect attempts left. Connection permanently lost.")
    # Optionally attempt to restart the entire WebSocket if desired, but be careful with loops.

def _connect_websocket_thread_target():
    """
    Target function for the WebSocket thread.
    It attempts to connect the WebSocket.
    Includes a workaround for signal handling issues on Windows in non-main threads.
    Gracefully handles connection errors.
    """
    global kws
    if kws:
        try:
            # On Windows, signal.signal can only be called from the main thread.
            # KiteTicker's underlying library (Twisted) attempts to install a SIGINT handler.
            # This hack temporarily disables SIGINT handling in this thread to avoid ValueError.
            # NOTE: This is a common workaround for this specific Twisted/threading issue on Windows.
            if sys.platform == 'win32':
                old_handler = None
                try:
                    # Try to ignore SIGINT temporarily if running on Windows
                    old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
                except ValueError:
                    # This can still fail if it's truly not the main thread of the main interpreter
                    # and another signal handler is already active.
                    logging.warning("Could not set SIG_IGN for SIGINT in WebSocket thread. "
                                    "Signal handling error might persist.")
                
                try:
                    kws.connect()
                finally:
                    if old_handler is not None:
                        try:
                            # Restore original handler after connect() returns
                            signal.signal(signal.SIGINT, old_handler) 
                        except ValueError:
                            logging.warning("Could not restore original SIGINT handler in WebSocket thread.")
            else:
                # For non-Windows platforms, connect directly
                kws.connect()
        except Exception as e:
            logging.error(f"Error connecting Kite WebSocket in thread: {e}")
    else:
        logging.error("KiteTicker instance not initialized for thread target.")


def start_websocket(api_k, access_t, initial_tokens=None):
    """
    Initializes and starts the KiteTicker WebSocket connection in a new daemon thread.
    This prevents the WebSocket from blocking the main Dash application thread.
    initial_tokens: A list of instrument tokens that should be subscribed immediately and persistently.
    """
    global kws, access_token_ws, api_key_ws, always_subscribed_tokens
    api_key_ws = api_k
    access_token_ws = access_t

    if not api_k or not access_t: # Check provided API key and access token
        logging.error("API Key or Access Token not provided for WebSocket. Cannot start.")
        return

    # If an existing kws instance is running, stop it first to prevent multiple connections
    if kws and kws.is_connected():
        logging.info("Existing Kite WebSocket connection found, stopping before new start.")
        kws.stop()
        time.sleep(1) # Give it a moment to shut down gracefully
    elif kws:
        # If kws exists but is not connected, it might be in a bad state, clear it.
        logging.info("Existing Kite WebSocket instance found but not connected, clearing it.")
        kws = None
        
    kws = KiteTicker(api_key_ws, access_token_ws)

    # Assign all defined callbacks to the new KiteTicker instance
    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close
    kws.on_error = on_error
    kws.on_reconnect = on_reconnect
    kws.on_noreconnect = on_noreconnect

    # Store initial tokens for persistent subscription
    if initial_tokens:
        always_subscribed_tokens.update(initial_tokens) # Add to the set

    # Start the WebSocket connection in a separate daemon thread.
    # A daemon thread will automatically exit when the main program exits.
    threading.Thread(target=_connect_websocket_thread_target, daemon=True).start()
    logging.info("Kite WebSocket connection attempt started in a new thread.")

def stop_websocket():
    """
    Stops the KiteTicker WebSocket connection if it's active.
    """
    global kws, always_subscribed_tokens
    if kws and kws.is_connected():
        logging.info("Stopping Kite WebSocket connection.")
        kws.stop()
        kws = None # Clear the instance reference
        always_subscribed_tokens.clear() # Clear persistent tokens on full stop/logout
    elif kws:
        logging.info("Kite WebSocket instance exists but is not connected. Clearing it.")
        kws = None
        always_subscribed_tokens.clear() # Clear persistent tokens
    else:
        logging.info("No active Kite WebSocket connection to stop.")

def subscribe_to_tokens(tokens):
    """
    Subscribes the WebSocket to a list of instrument tokens.
    It combines the provided tokens with the always_subscribed_tokens
    to ensure persistent subscriptions are maintained.
    """
    global kws, always_subscribed_tokens
    if kws and kws.is_connected():
        current_subscriptions = []
        # Attempt to get current subscriptions reliably.
        if hasattr(kws, 'subscriptions') and kws.subscriptions:
            if isinstance(kws.subscriptions, dict):
                current_subscriptions = list(kws.subscriptions.keys())
            elif isinstance(kws.subscriptions, (list, set)):
                current_subscriptions = list(kws.subscriptions)

        # Combine always subscribed tokens with the new tokens requested
        all_tokens_to_subscribe = list(set(list(always_subscribed_tokens) + tokens))
        
        # Determine tokens to unsubscribe (those currently subscribed but not in the new combined list)
        tokens_to_unsubscribe = list(set(current_subscriptions) - set(all_tokens_to_subscribe))
        
        # Determine tokens to subscribe (those in the new combined list but not currently subscribed)
        tokens_to_add = list(set(all_tokens_to_subscribe) - set(current_subscriptions))

        if tokens_to_unsubscribe:
            logging.info(f"Unsubscribing from {len(tokens_to_unsubscribe)} instruments.")
            try:
                kws.unsubscribe(tokens_to_unsubscribe)
            except Exception as e:
                logging.error(f"Error during unsubscribe: {e}")
        
        if tokens_to_add:
            logging.info(f"Subscribing to {len(tokens_to_add)} new instruments.")
            kws.subscribe(tokens_to_add)
            kws.set_mode(kws.MODE_FULL, tokens_to_add) # Request full mode for these new tokens
        else:
            logging.info("No new tokens to subscribe.")

    else:
        logging.warning("WebSocket not connected. Cannot subscribe to instruments.")

def get_live_quote(instrument_token):
    """
    Retrieves the latest tick data for a single instrument token from the live_quotes cache.
    """
    return live_quotes.get(instrument_token)

def get_all_live_quotes():
    """
    Retrieves all currently stored live tick data from the live_quotes cache.
    """
    return live_quotes

def clear_live_quotes():
    """
    Clears the global live_quotes dictionary.
    Useful when the user logs out or clears the token.
    """
    global live_quotes
    live_quotes = {}
    logging.info("Cleared live quotes cache.")
