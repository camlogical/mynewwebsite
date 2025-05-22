from dotenv import load_dotenv
load_dotenv()
import random
import os
import threading
import time
import requests
import re
import json
from datetime import datetime
from flask import Flask
from bs4 import BeautifulSoup
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram import ReplyKeyboardRemove
from telegram.constants import ChatAction
import gspread
from google.oauth2.service_account import Credentials
import asyncio
from datetime import datetime
import warnings
from telegram.warnings import PTBUserWarning
import logging
from logging.handlers import RotatingFileHandler
from datetime import date
from googleapiclient.http import MediaInMemoryUpload
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
import psutil
import platform
import socket
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import tempfile
from PIL import Image
import atexit
import telegram

# Set up logging
logger = logging.getLogger('TelegramLandChecker')
logger.setLevel(logging.DEBUG)

# Create log directory if it doesn't exist
if not os.path.exists('logs'):
    os.makedirs('logs')

class DailyRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename_pattern):
        self.filename_pattern = filename_pattern
        self.current_date = self.get_current_date()
        filename = self.get_filename()
        super().__init__(filename, encoding='utf-8')

    def get_current_date(self):
        return date.today().strftime('%Y-%m-%d')

    def get_filename(self):
        return self.filename_pattern.format(self.current_date)

    def emit(self, record):
        current_date = self.get_current_date()
        if current_date != self.current_date:
            # Date has changed, switch to new file
            self.current_date = current_date
            self.baseFilename = self.get_filename()
            # Create the file if it doesn't exist
            if not os.path.exists(self.baseFilename):
                open(self.baseFilename, 'a').close()
        super().emit(record)

# Create handlers with date in filename
log_format = logging.Formatter('%(asctime)s %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

# Daily rotating file handler
file_handler = DailyRotatingFileHandler('logs/debug_{}.log')
file_handler.setFormatter(log_format)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Function to ensure log file exists
def ensure_log_file_exists():
    today = date.today().strftime('%Y-%m-%d')
    log_file = f'logs/debug_{today}.log'
    if not os.path.exists(log_file):
        try:
            # Create the logs directory if it doesn't exist
            os.makedirs('logs', exist_ok=True)
            # Create the log file
            open(log_file, 'a').close()
            logger.info(f"Created new log file: {log_file}")
        except Exception as e:
            logger.error(f"Error creating log file: {str(e)}")

# Function to check and rotate logs periodically
def check_and_rotate_logs():
    while True:
        try:
            ensure_log_file_exists()
            # Sleep for 1 hour before next check
            time.sleep(3600)
        except Exception as e:
            logger.error(f"Error in log rotation check: {str(e)}")
            time.sleep(60)  # Wait a minute before retrying on error

# Start log rotation check thread
log_check_thread = threading.Thread(target=check_and_rotate_logs, daemon=True)
log_check_thread.start()

# Disable the specific warning about Application instances
warnings.filterwarnings('ignore', category=PTBUserWarning)

# === CONFIG ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SHEET_ID = "1N_LM9CM4egDeEVVbWx7GK8h5usQbg_EEDJZBNt8M1oY"
DEBUG_SHEET_ID = "1s_Y3qemK8IppJYed6HYBdI9U1HAZwslRf0ZvrmUNv_M"  # Separate sheet for debug logs
SHEET_TAB = "User_Search_History"
USER_CONTACT_TAB = "User_Contacts"
USER_DB_FILE = "users.json"
PREMIUM_USERS_TAB = "Premium_Users"
BANNED_USERS_TAB = "Banned_Users"  # Sheet tab for banned users

# === GLOBALS ===
user_database = {}
user_locks = {}
lock_activity = {}  # Track when locks were last used
last_upload_time = 0
RATE_LIMIT_DELAY = 1.2  # Delay between writes in seconds
start_time = datetime.now()  # Add this right after imports to track bot uptime
_gsheet_client = None  # Cache for Google Sheets client
premium_user_cache = {}  # Cache for premium user status
_browser = None
processing_messages = {}  # Track processing messages per user
error_messages = {}  # Track error messages per user
user_preferences = {}  # Track user preferences for screenshot display
user_last_search_time = {}  # Track last search time for each user
user_message_times = {}  # Track message timestamps for spam detection
SPAM_THRESHOLD = 3  # Number of messages within SPAM_WINDOW to trigger spam detection
SPAM_WINDOW = 5  # Time window in seconds to check for spam
SPAM_FREEZE = 10  # Freeze time in seconds for spammers
banned_users_cache = {}  # Cache for banned users

# === USER PREFERENCES ===
def get_user_preference(user_id: str) -> bool:
    """Get user's preference for screenshot display (True = with screenshot, False = without)"""
    preference = user_preferences.get(user_id, True)  # Default to True (with screenshot) for premium users
    logger.debug(f"Getting preference for user {user_id}: {preference}")
    return preference

def set_user_preference(user_id: str, with_screenshot: bool):
    """Set user's preference for screenshot display"""
    logger.debug(f"Setting preference for user {user_id} to {with_screenshot}")
    user_preferences[user_id] = with_screenshot

async def choose_preference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /choose command for premium users to toggle their screenshot preference"""
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if not is_premium_user(user_id):
        error_msg = await update.message.reply_text(
            "❌ This feature is only available for premium users.\n"
            "Contact @Code404_SH to become a premium user."
        )
        error_messages[user_id] = error_msg
        return

    # Create inline keyboard with two options
    keyboard = [
        [
            InlineKeyboardButton("📸 Results + Screenshots", callback_data="pref_with_screenshot"),
            InlineKeyboardButton("📝 Results Only", callback_data="pref_without_screenshot")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    current_preference = get_user_preference(user_id)
    preference_text = "📸 with screenshots" if current_preference else "📝 without screenshots"
    
    # Store the choose message for later deletion
    choose_msg = await update.message.reply_text(
        f"🔧 *Choose your preferred result format*\n\n"
        f"Current preference: {preference_text}\n\n"
        f"Select your preferred option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    # Store the message ID for later deletion
    if 'choose_messages' not in context.user_data:
        context.user_data['choose_messages'] = []
    context.user_data['choose_messages'].append(choose_msg.message_id)

async def handle_preference_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the callback from preference selection buttons"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if not is_premium_user(user_id):
        await query.answer("❌ This feature is only available for premium users.", show_alert=True)
        return

    try:
        # First answer the callback query to remove the loading state
        await query.answer()
        
        if query.data == "pref_with_screenshot":
            set_user_preference(user_id, True)
            preference_text = "📸 with screenshots"
        elif query.data == "pref_without_screenshot":
            set_user_preference(user_id, False)
            preference_text = "📝 without screenshots"
        else:
            return

        # Create new keyboard with updated state
        keyboard = [
            [
                InlineKeyboardButton("📸 Results + Screenshots", callback_data="pref_with_screenshot"),
                InlineKeyboardButton("📝 Results Only", callback_data="pref_without_screenshot")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Update the message to show the new preference
        await query.edit_message_text(
            f"🔧 *Choose your preferred result format*\n\n"
            f"Current preference: {preference_text}\n\n"
            f"Select your preferred option:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    except Exception as e:
        await query.answer("❌ Error updating preference", show_alert=True)

def get_debug_log_tab_name():
    """Get the debug log tab name for the current date"""
    today = date.today().strftime('%Y_%m_%d')  # Format: YYYY_MM_DD
    return f"Debug_Logs_{today}"

def upload_log_to_sheet():
    try:
        global last_upload_time
        
        # Get credentials and sheet client
        client = get_gsheet_client()
        
        # Get today's tab name
        debug_tab_name = get_debug_log_tab_name()
        
        try:
            # Try to get today's debug worksheet
            sheet = client.open_by_key(DEBUG_SHEET_ID).worksheet(debug_tab_name)
        except gspread.exceptions.WorksheetNotFound:
            # If it doesn't exist, create it with headers
            sheet = client.open_by_key(DEBUG_SHEET_ID).add_worksheet(title=debug_tab_name, rows="1000", cols="4")
            sheet.append_row(["Timestamp", "Log Level", "Message", "Additional Info"])
            logger.info(f"Created new debug log sheet for {debug_tab_name}")
        
        # Get today's log file
        today = date.today().strftime('%Y-%m-%d')
        log_file = f'logs/debug_{today}.log'
        
        if not os.path.exists(log_file):
            logger.warning(f"Log file does not exist: {log_file}")
            return

        # Read and process log file
        with open(log_file, 'r', encoding='utf-8') as f:
            log_lines = f.readlines()

        # Process each log line with rate limiting
        batch_size = 10  # Process logs in batches
        current_batch = []
        
        for line in log_lines:
            try:
                # Parse log line (format: timestamp - level - message)
                parts = line.strip().split(' - ', 2)
                if len(parts) == 3:
                    timestamp, level, message = parts
                    
                    # Clean up the message
                    message = message.replace('\n', ' ').strip()
                    additional_info = ''
                    
                    # If message contains error details, split them
                    if ': ' in message:
                        message, additional_info = message.split(': ', 1)
                    
                    # Add to current batch
                    current_batch.append([timestamp, level, message, additional_info])
                    
                    # When batch is full or it's the last line, upload the batch
                    if len(current_batch) >= batch_size or line == log_lines[-1]:
                        # Respect rate limiting
                        current_time = time.time()
                        time_since_last_upload = current_time - last_upload_time
                        if time_since_last_upload < RATE_LIMIT_DELAY:
                            time.sleep(RATE_LIMIT_DELAY - time_since_last_upload)
                        
                        # Upload batch
                        sheet.append_rows(current_batch)
                        last_upload_time = time.time()
                        
                        # Clear batch
                        current_batch = []
                    
            except Exception as e:
                logger.error(f"Error processing log line: {str(e)}")
                continue
            
        logger.info(f"Successfully uploaded logs to {debug_tab_name}")
            
    except Exception as e:
        logger.error(f"Error in upload_log_to_sheet: {str(e)}", exc_info=True)

# Function to periodically upload logs
def auto_upload_logs():
    logger.info("Starting auto upload logs thread")
    while True:
        try:
            logger.info("Attempting periodic log upload")
            upload_log_to_sheet()
            time.sleep(300)  # Upload every 5 minutes
        except Exception as e:
            logger.error(f"Error in auto_upload_logs thread: {str(e)}", exc_info=True)
            time.sleep(60)  # Wait a minute before retrying on error

# === FLASK SETUP ===
app = Flask(__name__)

@app.route('/')
def home():
    logger.debug("Flask home route accessed")
    return "✅ Bot is running!"

def run_flask():
    logger.info("Starting Flask server")
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def auto_ping():
    url = os.getenv("PING_URL")
    if not url:
        logger.warning("⚠ No PING_URL set. Skipping auto-ping.")
        return
    while True:
        try:
            logger.debug(f"Pinging {url}")
            requests.get(url)
        except Exception as e:
            logger.error(f"Ping failed: {str(e)}")
        time.sleep(600)

# === GOOGLE SHEETS CLIENT ===
def get_gsheet_client():
    global _gsheet_client
    if _gsheet_client is None:
        logger.debug("Initializing Google Sheets client")
        credentials_info = json.loads(os.getenv('GOOGLE_CREDENTIALS_JSON'))
        _gsheet_client = gspread.service_account_from_dict(credentials_info, scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ])
    return _gsheet_client

# === SAVE & LOAD USER DATABASE ===
def save_user_database():
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(user_database, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Saved {len(user_database)} users to database")
    except Exception as e:
        logger.error(f"❌ Failed to save user database: {str(e)}")

def load_user_database():
    global user_database
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                user_database = json.load(f)
            logger.info(f"✅ Loaded {len(user_database)} users from {USER_DB_FILE}")
        except Exception as e:
            logger.error(f"❌ Failed to load user database: {str(e)}")

def save_all_users_to_gsheet():
    try:
        if not user_database:
            print("⚠️ No users to save.")
            return

        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet(USER_CONTACT_TAB)

        existing_data = sheet.get_all_records()
        existing_user_ids = {str(row["user_id"]) for row in existing_data}

        new_entries = 0
        for user_id, info in user_database.items():
            if str(user_id) not in existing_user_ids:
                sheet.append_row([
                    str(user_id),
                    info.get("username", "Unknown"),
                    info.get("full_name", "Unknown"),
                    info.get("phone_number", "Unknown")
                ])
                new_entries += 1

        print(f"✅ Saved {new_entries} new users to Google Sheet.")
    except Exception as e:
        print(f"❌ Failed to save users to Google Sheet: {e}")


# === SAVE SEARCH HISTORY TO GOOGLE SHEET ===
def save_user_search_and_log(user_id, username, land_number, result):
    try:
        # Get client once for both operations
        client = get_gsheet_client()
        
        # Prepare all data first
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_info = user_database.get(str(user_id), {})
        
        # Prepare rows for batch update
        search_history_row = [
            str(user_id), username, user_info.get("full_name", ""),
            user_info.get("phone_number", ""), land_number, timestamp
        ]
        
        owner_info_str = ""
        if isinstance(result.get("owner_info"), dict):
            owner_info_str = "; ".join(f"{k}: {v}" for k, v in result["owner_info"].items())
            
        full_log_row = [
            str(user_id), username, user_info.get("full_name", ""),
            user_info.get("phone_number", ""), land_number, timestamp,
            result.get("status", ""), result.get("serial_info", ""),
            result.get("location", ""), result.get("updated_system", ""),
            owner_info_str
        ]
        
        # Batch update both sheets
        sheets = {
            SHEET_TAB: [search_history_row],
            "Full_Search_Logs": [full_log_row]
        }
        
        for sheet_name, rows in sheets.items():
            try:
                sheet = client.open_by_key(SHEET_ID).worksheet(sheet_name)
                sheet.append_rows(rows)
            except gspread.exceptions.WorksheetNotFound:
                if sheet_name == "Full_Search_Logs":
                    sheet = client.open_by_key(SHEET_ID).add_worksheet(
                        title=sheet_name, rows="1000", cols="20"
                    )
                    sheet.append_row([
                        "user_id", "username", "full_name", "phone_number",
                        "land_number", "timestamp", "status",
                        "serial_info", "location", "updated_system", "owner_info"
                    ])
                sheet.append_rows(rows)
                
    except Exception as e:
        logger.error(f"Failed to save search data - User: {username}, Land: {land_number}, Error: {str(e)}")

# === RANDOM USER AGENTS ===
def load_user_agents(filepath="user_agents.txt"):
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

USER_AGENTS_URL = os.getenv("USER_AGENTS_URL")

def fetch_user_agents(url: str) -> list:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return [line.strip() for line in response.text.splitlines() if line.strip()]
    except Exception as e:
        print(f"Error fetching user agents: {e}")
        return []

USER_AGENTS = fetch_user_agents(USER_AGENTS_URL)

def get_random_user_agent():
    return random.choice(USER_AGENTS) if USER_AGENTS else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

# === SCRAPER ===
def scrape_land_data(land_number: str) -> dict:
    if not re.match(r'^\d{8}-\d{4}$', land_number):
        logger.debug(f"Invalid Land Number Format: {land_number}")
        return {"status": "error", "message": "អ្នកវាយទម្រង់លេខក្បាលដីខុស។\n សូមវាយជាទម្រង់ ########-#### \nឧទា.18020601-0001"}
    url = os.getenv("URL")
    user_agent = get_random_user_agent()
    logger.debug(f"Using User-Agent for search: {user_agent}")
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9,km-KH;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://miniapp.mlmupc.gov.kh/",
        "Connection": "keep-alive"
    }
    data = {"recaptchaToken": "", "landNum": land_number}

    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        if response.status_code != 200:
            logger.error(f"HTTP Error for Land {land_number}: {response.status_code}")
            return {"status": "error", "message": f"HTTP error {response.status_code}"}

        html = response.text

        if "មិនមានព័ត៌មានអំពីក្បាលដីនេះទេ" in html:
            logger.debug(f"Land Not Found: {land_number}")
            return {"status": "not_found", "message": "មិនមានព័ត៌មានអំពីក្បាលដីនេះទេ។"}

        if "វិញ្ញាបនបត្រសម្គាល់ម្ចាស់អចលនវត្ថុលេខ" in html:
            logger.debug(f"Land Found: {land_number}")
            status = "found"
        else:
            logger.debug(f"Land Not Found: {land_number}")
            return {"status": "not_found", "message": "មិនមានព័ត៌មានអំពីក្បាលដីនេះទេ។"}

        def extract_between(text, left, right):
            try:
                return text.split(left)[1].split(right)[0].strip()
            except:
                return ""

        serial_info = extract_between(html, 'id="serail_info">', '</span></td>')
        location = extract_between(html, '<span>ភូមិ ៖ ', '</span>')
        updated_system = extract_between(html, '(ធ្វើបច្ចុប្បន្នភាព: <span>', '</span>)</p>')

        owner_info = {}
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find("table", class_="table table-bordered")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    owner_info[key] = value

        return {
            "status": status,
            "serial_info": serial_info,
            "location": location,
            "updated_system": updated_system,
            "owner_info": owner_info
        }
    except Exception as e:
        logger.error(f"Scraping Error for Land {land_number}: {str(e)}")
        return {"status": "error", "message": str(e)}

# === USER LOCK ===
def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = threading.Lock()
        lock_activity[user_id] = time.time()
    return user_locks[user_id]

def update_lock_activity(user_id):
    """Update the last activity time for a user's lock"""
    if user_id in lock_activity:
        lock_activity[user_id] = time.time()

def cleanup_old_locks():
    """Clean up locks for users who haven't been active for a while"""
    current_time = time.time()
    for user_id in list(user_locks.keys()):
        if current_time - lock_activity.get(user_id, 0) > 300:  # 5 minutes
            del user_locks[user_id]
            del lock_activity[user_id]

# Add periodic lock cleanup
def start_lock_cleanup():
    while True:
        try:
            cleanup_old_locks()
            time.sleep(300)  # Run every 5 minutes
        except Exception as e:
            logger.error(f"Error in lock cleanup: {str(e)}")
            time.sleep(60)

# Start the lock cleanup thread
lock_cleanup_thread = threading.Thread(target=start_lock_cleanup, daemon=True)
lock_cleanup_thread.start()

# === BOT COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)

    # Remove any existing error message
    await remove_error_message(user_id)

    # First, quickly check local database
    if user_id in user_database:
        await update.message.reply_text(
            "🏡 សូមស្វាគមន៍មកកាន់កម្មវិធីស្វែងរកព័ត៌មានអំពីក្បាលដី (MLMUPC Land info Checker Bot!)\n\n"
            "សូមវាយជាទម្រង់ ########-#### \nឧទា.18020601-0001\n\n"
            "ចុច /help ដើម្បីមើលរបៀបប្រើប្រាស់ និងមុខងារផ្សេងៗ\n\n"
            "Bot Developed with ❤️ by MNPT."
        )
        return

    # If not in local database, check Google Sheet in background
    async def check_google_sheet():
        try:
            client = get_gsheet_client()
            sheet = client.open_by_key(SHEET_ID).worksheet(USER_CONTACT_TAB)
            user_data = sheet.get_all_records()
            user_in_sheet = any(str(user['user_id']) == user_id for user in user_data)
            
            if user_in_sheet:
                # Add to local database for future quick checks
                user_row = next((user for user in user_data if str(user['user_id']) == user_id), None)
                if user_row:
                    user_database[user_id] = {
                        "username": user_row.get("username", "Unknown"),
                        "full_name": user_row.get("full_name", "Unknown"),
                        "phone_number": user_row.get("phone_number", "Unknown")
                    }
                    save_user_database()
                return user_in_sheet
            return False
        except Exception as e:
            logger.error(f"Error checking Google Sheet: {str(e)}")
            return False

    # Check Google Sheet for existing user
    is_registered = await check_google_sheet()
    
    if is_registered:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        await asyncio.sleep(0.1)  # Optional delay
        await update.message.reply_text(
            "🏡 សូមស្វាគមន៍មកកាន់កម្មវិធីស្វែងរកព័ត៌មានអំពីក្បាលដី (MLMUPC Land info Checker Bot!)\n\n"
            "សូមវាយជាទម្រង់ ########-#### \nឧទា.18020601-0001\n\n"
            "ចុច /help ដើម្បីមើលរបៀបប្រើប្រាស់ និងមុខងារផ្សេងៗ\n\n"
            "Bot Developed with ❤️ by MNPT."
        )
    else:
        button = KeyboardButton(text="✅ VERIFY", request_contact=True)
        reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
        error_msg = await update.message.reply_text("ដើម្បីប្រើប្រាស់សូមចុចប៊ូតុងខាងក្រោមដើម្បីបញ្ជាក់", reply_markup=reply_markup)
        error_messages[user_id] = error_msg

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    contact = update.message.contact
    username = update.message.from_user.username or "Unknown"
    full_name = update.message.from_user.full_name or "Unknown"
    phone_number = contact.phone_number

    user_database[user_id] = {
        "username": username,
        "full_name": full_name,
        "phone_number": phone_number
    }
    save_user_database()
    save_all_users_to_gsheet()

    await update.message.reply_text(
        "✅ បានបញ្ជាក់ព័ត៌មានរបស់អ្នកជោគជ័យ ✅\n\n\n"
        "🏡 សូមស្វាគមន៍មកកាន់កម្មវិធីស្វែងរកព័ត៌មានអំពីក្បាលដី (MLMUPC Land info Checker Bot!)\n\n"
        "សូមវាយជាទម្រង់ ########-#### \nឧទា.18020601-0001\n\n"
        "ចុច /help ដើម្បីមើលរបៀបប្រើប្រាស់ និងមុខងារផ្សេងៗ\n\n"
        "Bot Developed with ❤️ by MNPT.",
        reply_markup=ReplyKeyboardRemove()
    )

def get_browser():
    """Get or create a browser instance"""
    global _browser
    try:
        # Check if browser exists and is responsive
        if _browser is not None:
            try:
                _browser.current_url
                return _browser
            except:
                # Browser exists but is not responsive
                try:
                    _browser.quit()
                except:
                    pass
                _browser = None
        
        # Create new browser instance if needed
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        
        # Performance optimizations
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--disable-gpu-compositing')
        chrome_options.add_argument('--disable-gpu-rasterization')
        chrome_options.add_argument('--disable-gpu-sandbox')
        chrome_options.add_argument('--disable-accelerated-2d-canvas')
        chrome_options.add_argument('--disable-accelerated-jpeg-decoding')
        chrome_options.add_argument('--disable-accelerated-mjpeg-decode')
        chrome_options.add_argument('--disable-accelerated-video-decode')
        chrome_options.add_argument('--disable-d3d11')
        chrome_options.add_argument('--disable-webgl')
        chrome_options.add_argument('--disable-webgl2')
        
        # Memory and performance optimizations
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        chrome_options.add_argument('--force-device-scale-factor=1')
        chrome_options.add_argument('--disable-reading-from-canvas')
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--disable-site-isolation-trials')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        
        # Additional performance settings
        chrome_options.add_argument('--disable-dev-tools')
        chrome_options.add_argument('--no-default-browser-check')
        chrome_options.add_argument('--no-first-run')
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-notifications')
        chrome_options.add_argument('--disable-popup-blocking')
        
        # Set logging level to exclude GPU errors
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_argument('--silent')

        # Check if running in Docker
        is_docker = os.path.exists('/.dockerenv')
        
        if is_docker:
            # Use system-installed ChromeDriver in Docker
            service = Service('/usr/local/bin/chromedriver')
            logger.info("Running in Docker - Using system ChromeDriver")
        else:
            # Use ChromeDriverManager for local development
            service = Service(ChromeDriverManager().install())
            logger.info("Running locally - Using ChromeDriverManager")
        
        _browser = webdriver.Chrome(service=service, options=chrome_options)
        _browser.set_window_size(1920, 1080)
        return _browser
    except Exception as e:
        logger.error(f"Error creating browser instance: {str(e)}")
        return None

async def take_mlmupc_screenshot(land_number: str) -> str:
    """Take a screenshot of the MLMUPC website for the given land number."""
    try:
        # Get browser instance
        driver = get_browser()
        if not driver:
            logger.error("Failed to get browser instance")
            return None
            
        try:
            # Navigate to the MLMUPC website
            url = os.getenv("URL")
            driver.get(url)
            
            # Wait for the page to load with shorter timeout
            try:
                # Wait for the input field with reduced timeout
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "landNum"))
                )
                
                # Find and fill the land number input
                land_input = driver.find_element(By.ID, "landNum")
                land_input.clear()  # Clear any existing input
                land_input.send_keys(land_number)
                
                # Find and click the search button
                search_button = driver.find_element(By.XPATH, "//button[contains(text(), 'ស្វែងរក')]")
                search_button.click()
                
                # Wait for results to load with optimized timeout
                try:
                    # Wait for the content div with reduced timeout
                    content_div = WebDriverWait(driver, 8).until(
                        EC.visibility_of_element_located((By.CSS_SELECTOR, "#content > div:nth-child(2)"))
                    )
                    
                    # Small delay for content rendering
                    await asyncio.sleep(0.5)
                    
                    # Get the element's location and size
                    location = content_div.location
                    size = content_div.size
                    
                    # Create a temporary file with a unique name
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    temp_file = tempfile.NamedTemporaryFile(suffix=f'_land_{land_number}_{timestamp}.png', delete=False)
                    screenshot_path = temp_file.name
                    temp_file.close()
                    
                    # Take screenshot
                    driver.save_screenshot(screenshot_path)
                    
                    # Process the screenshot
                    if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 0:
                        try:
                            # Open and process the image
                            img = Image.open(screenshot_path)
                            
                            # Calculate the crop box with padding
                            h_crop_padding = 8
                            v_crop_padding = 15
                            left = max(0, location['x'] - h_crop_padding)
                            top = max(0, location['y'] - h_crop_padding)
                            right = min(img.width, location['x'] + size['width'] + h_crop_padding)
                            bottom = min(img.height, location['y'] + size['height'] + h_crop_padding)
                            
                            # Crop the image
                            img = img.crop((left, top, right, bottom))
                            
                            # Handle minimum width requirement
                            min_width = 800
                            if img.width < min_width:
                                h_padding = (min_width - img.width) // 2
                                v_padding = v_crop_padding
                                new_width = min_width
                                new_height = img.height + (v_padding * 2)
                                new_img = Image.new('RGB', (new_width, new_height), 'white')
                                new_img.paste(img, (h_padding, v_padding))
                                img = new_img
                            else:
                                v_padding = v_crop_padding
                                new_height = img.height + (v_padding * 2)
                                new_img = Image.new('RGB', (img.width, new_height), 'white')
                                new_img.paste(img, (0, v_padding))
                                img = new_img
                            
                            # Save with optimization
                            img.save(screenshot_path, quality=95, optimize=True)
                            logger.info(f"Screenshot saved to temp file: {screenshot_path}")
                            return screenshot_path
                            
                        except Exception as e:
                            logger.error(f"Error processing screenshot: {str(e)}")
                            if os.path.exists(screenshot_path):
                                os.unlink(screenshot_path)
                            return None
                    else:
                        logger.error("Screenshot file is empty or does not exist")
                        if os.path.exists(screenshot_path):
                            os.unlink(screenshot_path)
                        return None
                        
                except Exception as e:
                    logger.error(f"Error waiting for results: {str(e)}")
                    return None
                
            except Exception as e:
                logger.error(f"Error during page interaction: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Error in screenshot process: {str(e)}")
            return None
            
    except Exception as e:
        logger.error(f"Error in take_mlmupc_screenshot: {str(e)}")
        return None

# Add cleanup function for browser
def cleanup_browser():
    global _browser
    if _browser is not None:
        try:
            _browser.quit()
        except:
            pass
        _browser = None

# Add this to your main shutdown or cleanup routine
atexit.register(cleanup_browser)

async def handle_multiple_land_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or update.message.from_user.full_name or "Unknown"
    current_time = time.time()

    # Remove any existing error message
    await remove_error_message(user_id)

    # Check if user is banned
    if is_user_banned(user_id):
        error_msg = await update.message.reply_text(
            "⚠️ You have been banned from using this bot.\n"
            "Please contact the administrator if you believe this is a mistake."
        )
        error_messages[user_id] = error_msg
        return

    # First check local memory
    if user_id not in user_database:
        # Fallback to Google Sheet check
        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet(USER_CONTACT_TAB)
        user_data = sheet.get_all_records()

        user_row = next((user for user in user_data if str(user['user_id']) == user_id), None)
        if user_row:
            user_database[user_id] = {
                "username": user_row.get("username", "Unknown"),
                "full_name": user_row.get("full_name", "Unknown"),
                "phone_number": user_row.get("phone_number", "Unknown")
            }
            save_user_database()
        else:
            button = KeyboardButton(text="✅ VERIFY", request_contact=True)
            reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
            error_msg = await update.message.reply_text("ដើម្បីប្រើប្រាស់សូមចុចប៊ូតុងខាងក្រោមដើម្បីបញ្ជាក់", reply_markup=reply_markup)
            error_messages[user_id] = error_msg
            return

    lock = get_user_lock(user_id)
    update_lock_activity(user_id)  # Update activity time when getting lock

    if not lock.acquire(blocking=False):
        logger.warning(f"Search Blocked (Busy) - User: {username} (ID: {user_id})")
        error_msg = await update.message.reply_text("⚠️ប្រព័ន្ធកំពុងរវល់⚠️\nសូមមេត្តារង់ចាំ ឬសូមសាកល្បងស្វែងរកម្ដងទៀត។")
        error_messages[user_id] = error_msg
        return

    try:
        # Delete any existing choose messages
        if 'choose_messages' in context.user_data:
            for msg_id in context.user_data['choose_messages']:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
                except Exception:
                    pass
            context.user_data['choose_messages'] = []

        # Delete any notification messages
        if 'notification_messages' in context.user_data:
            for msg_id in context.user_data['notification_messages']:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
                except Exception:
                    pass
            context.user_data['notification_messages'] = []

        land_numbers = update.message.text.strip().split("\n")
        
        # Check number of land numbers for non-premium users
        if not is_premium_user(user_id) and len(land_numbers) > 1:
            error_msg = await update.message.reply_text(
                "⚠️ Normal users can only search one land number at a time.\n"
                "Please contact Admin <a href='https://t.me/Code404_SH'>@Code404_SH</a> to become a premium user.",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            error_messages[user_id] = error_msg
            return

        # Initialize processing messages list for this user
        if user_id not in processing_messages:
            processing_messages[user_id] = []

        for land_number in land_numbers:
            land_number = land_number.strip()
            update_lock_activity(user_id)  # Update activity time for each search
            
            # Log only essential information
            logger.debug(f"Search started - User: {username}, Land: {land_number}")
            
            # Send processing message
            processing_msg = await update.message.reply_text(
                f"_🔍 Searching for land number {land_number}..._",
                parse_mode="Markdown",
                disable_notification=True
            )
            processing_messages[user_id].append(processing_msg)
            
            # Only show screenshot processing message for premium users with screenshot preference
            if is_premium_user(user_id) and get_user_preference(user_id):
                screenshot_msg = await update.message.reply_text(
                    "_📸 Capturing screenshot..._",
                    parse_mode="Markdown",
                    disable_notification=True
                )
                processing_messages[user_id].append(screenshot_msg)
            
            # Perform the search
            result = scrape_land_data(land_number)
            
            # Log only essential information
            logger.debug(f"Search completed - User: {username}, Land: {land_number}, Status: {result['status']}")

            if result["status"] == "found":
                msg = f"✅ *Land Info Found for {land_number}*\n\n" \
                      f"⏰ *បច្ចុប្បន្នភាព៖* {result.get('updated_system', 'N/A')}\n" \
                      f"👉 *លេខប័ណ្ណកម្មសិទ្ធិ៖* {result.get('serial_info', 'N/A')}\n" \
                      f"📍 *ទីតាំងដី ភូមិ៖* {result.get('location', 'N/A')}\n"

                if result['owner_info']:
                    msg += "\n📝 *ព័ត៌មានក្បាលដី៖*\n"
                    for key, value in result['owner_info'].items():
                        msg += f"   - {key} {value}\n"

                msg += "\n\nChecked from: [MLMUPC](https://mlmupc.gov.kh/electronic-cadastral-services)\nBot Developed by MNPT"
                
                # Take and send screenshot only for premium users with screenshot preference
                if is_premium_user(user_id) and get_user_preference(user_id):
                    screenshot_path = await take_mlmupc_screenshot(land_number)
                    if screenshot_path and os.path.exists(screenshot_path):
                        try:
                            with open(screenshot_path, 'rb') as photo:
                                await update.message.reply_photo(
                                    photo=photo,
                                    caption=msg,
                                    parse_mode="Markdown"
                                )
                        finally:
                            # Clean up the temporary file
                            try:
                                os.unlink(screenshot_path)
                            except Exception as e:
                                logger.error(f"Error deleting temp file {screenshot_path}: {str(e)}")
                    else:
                        await update.message.reply_text(msg, parse_mode="Markdown")
                else:
                    await update.message.reply_text(msg, parse_mode="Markdown")

            elif result["status"] == "not_found":
                msg = f"⚠️ *{land_number}* {result.get('message', 'មិនមានព័ត៌មានអំពីក្បាលដីនេះទេ.')}"
                
                # Take and send screenshot only for premium users with screenshot preference
                if is_premium_user(user_id) and get_user_preference(user_id):
                    screenshot_path = await take_mlmupc_screenshot(land_number)
                    if screenshot_path and os.path.exists(screenshot_path):
                        try:
                            with open(screenshot_path, 'rb') as photo:
                                await update.message.reply_photo(
                                    photo=photo,
                                    caption=msg,
                                    parse_mode="Markdown"
                                )
                        finally:
                            # Clean up the temporary file
                            try:
                                os.unlink(screenshot_path)
                            except Exception as e:
                                logger.error(f"Error deleting temp file {screenshot_path}: {str(e)}")
                    else:
                        await update.message.reply_text(msg, parse_mode="Markdown")
                else:
                    await update.message.reply_text(msg, parse_mode="Markdown")

            else:
                msg = f"❌ Error for *{land_number}*: {result.get('message', 'Unknown error')}."
                error_msg = await update.message.reply_text(msg, parse_mode="Markdown")
                error_messages[user_id] = error_msg
                return

            # Delete all processing messages after sending the result
            if user_id in processing_messages:
                for msg in processing_messages[user_id]:
                    try:
                        await msg.delete()
                    except Exception as e:
                        logger.error(f"Error deleting processing message: {str(e)}")
                processing_messages[user_id] = []

            # Save search log after showing results
            save_user_search_and_log(user_id, username, land_number, result)

    except Exception as e:
        logger.error(f"Search Error - User: {username}, Error: {str(e)}")
        # Clean up any remaining processing messages on error
        if user_id in processing_messages:
            for msg in processing_messages[user_id]:
                try:
                    await msg.delete()
                except:
                    pass
            processing_messages[user_id] = []
    finally:
        lock.release()
        # Clean up processing messages list for this user
        if user_id in processing_messages:
            processing_messages[user_id] = []


# === HISTORY COMMANDS ===

async def get_history_text(context) -> str:
    try:
        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Full_Search_Logs")
        rows = sheet.get_all_values()
        
        if len(rows) <= 1:  # Only header or empty
            return "No search history found."
            
        # Get last 5 searches
        recent_searches = rows[-5:] if len(rows) > 5 else rows[1:]
        
        message = "📋 Recent Searches:\n\n"
        for row in reversed(recent_searches):
            try:
                # Get data from Full_Search_Logs columns
                user_id = row[0]
                username = row[1]
                full_name = row[2]
                land_number = row[4]
                timestamp = row[5]
                status = row[6]  # This is directly "found", "not_found", or "error"
                
                # Use full_name if username is unknown or empty
                display_name = username
                if not username or username.lower() in ['unknown', 'none', '']:
                    display_name = full_name
                
                # Add @ symbol to username if it exists
                if display_name and display_name.lower() not in ['unknown', 'none', '']:
                    display_name = f"@{display_name}"
                
                # Format the status based on the actual value
                if status == "found":
                    status_display = "✅ Found"
                elif status == "not_found":
                    status_display = "❌ Not Found"
                elif status == "error":
                    status_display = "⚠️ Invalid Format"
                else:
                    status_display = "❓ Unknown"
                
                message += f"👤 User: {display_name}\n"
                message += f"🔢 Land Number: {land_number}\n"
                message += f"📊 Status: {status_display}\n"
                message += f"⏰ Time: {timestamp}\n"
                message += "➖➖➖➖➖➖➖➖\n"
                
            except Exception as row_error:
                logging.error(f"Error processing row: {str(row_error)}")
                continue
            
        return message

    except Exception as e:
        logging.error(f"Error getting history: {str(e)}")
        return f"❌ Error getting history: {str(e)}"

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message and update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    try:
        # Send processing message
        if update.message:
            processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        else:
            processing_msg = await update.callback_query.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        history_text = await get_history_text(context)
        
        # Delete processing message
        await processing_msg.delete()
        
        if update.message:
            await update.message.reply_text(history_text)
        else:
            await update.callback_query.message.reply_text(history_text)
    except Exception as e:
        error_message = f"❌ Error getting history: {str(e)}"
        if update.message:
            error_msg = await update.message.reply_text(error_message)
            error_messages[user_id] = error_msg
        else:
            error_msg = await update.callback_query.message.reply_text(error_message)
            error_messages[user_id] = error_msg

# === BROADCAST COMMANDS ===
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: /broadcast <your message>")
        return

    message = " ".join(context.args)

    try:
        # Get the Google Sheet and user records
        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet(USER_CONTACT_TAB)
        user_records = sheet.get_all_records()  # This returns a list of dicts

        success = 0
        failed = 0

        # Iterate through user records instead of undefined 'users'
        for user in user_records:
            user_id = user.get("user_id")
            if user_id:
                try:
                    await context.bot.send_message(chat_id=int(user_id), text=message)
                    success += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"❌ Failed to send to {user_id}: {e}")
                    failed += 1

        await update.message.reply_text(
            f"📢 Broadcast complete.\n✅ Sent: {success}\n❌ Failed: {failed}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error broadcasting: {str(e)}")

# Modified function to handle premium users
def ensure_premium_sheet_exists():
    """Ensure premium sheet exists with correct headers"""
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Check if Premium_Users worksheet exists
        try:
            sheet = spreadsheet.worksheet(PREMIUM_USERS_TAB)
            # Check headers
            headers = sheet.row_values(1)
            expected_headers = ["user_id", "username", "full_name", "added_date"]
            
            # If headers don't match, update them
            if headers != expected_headers:
                sheet.clear()  # Clear the sheet
                sheet.append_row(expected_headers)  # Add correct headers
                logger.info(f"Updated {PREMIUM_USERS_TAB} headers")
                
        except gspread.exceptions.WorksheetNotFound:
            # Create new worksheet with correct headers
            sheet = spreadsheet.add_worksheet(title=PREMIUM_USERS_TAB, rows=1000, cols=4)
            sheet.append_row(["user_id", "username", "full_name", "added_date"])
            logger.info(f"Created new {PREMIUM_USERS_TAB} worksheet")
            
        return sheet
            
    except Exception as e:
        logger.error(f"Error ensuring premium sheet exists: {str(e)}")
        return None

async def add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.message.from_user.id
    if admin_id != ADMIN_ID:
        logger.warning(f"Unauthorized premium add attempt by user ID: {admin_id}")
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[str(admin_id)] = error_msg
        return

    if not context.args or len(context.args) < 1:
        logger.warning(f"Invalid add_premium command usage by admin")
        error_msg = await update.message.reply_text("⚠️ Usage: /addpremium <user_id>")
        error_messages[str(admin_id)] = error_msg
        return

    try:
        # Send processing message
        processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        user_id = context.args[0]
        logger.debug(f"Adding premium user - User ID: {user_id}, Requested by admin: {admin_id}")
        
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Get user info from contacts
        try:
            contact_sheet = spreadsheet.worksheet(USER_CONTACT_TAB)
            user_data = contact_sheet.get_all_records()
            user_info = next((user for user in user_data if str(user['user_id']) == str(user_id)), None)
            
            if not user_info:
                logger.warning(f"Failed to add premium - User {user_id} not found in contacts")
                error_msg = await update.message.reply_text(f"⚠️ User {user_id} not found in contacts. User must use the bot first.")
                error_messages[str(admin_id)] = error_msg
                return
                
            username = user_info.get('username', 'Unknown')
            full_name = user_info.get('full_name', 'Unknown')
            display_name = username if username != 'Unknown' else full_name
            
        except Exception as e:
            logger.error(f"Error accessing contacts for premium add - User ID: {user_id}, Error: {str(e)}")
            error_msg = await update.message.reply_text("❌ Error accessing user contacts.")
            error_messages[str(admin_id)] = error_msg
            return
        
        # Handle premium sheet
        try:
            sheet = spreadsheet.worksheet(PREMIUM_USERS_TAB)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=PREMIUM_USERS_TAB, rows=1000, cols=4)
            sheet.append_row(["user_id", "username", "full_name", "added_date"])
            logger.info(f"Created new {PREMIUM_USERS_TAB} worksheet for premium add")
        
        # Check if already premium
        premium_users = sheet.col_values(1)
        if user_id in premium_users:
            logger.info(f"Premium add skipped - User already premium: {display_name} (ID: {user_id})")
            error_msg = await update.message.reply_text(f"⚠️ User {display_name} (ID: {user_id}) is already a premium user.")
            error_messages[str(admin_id)] = error_msg
            return

        # Add new premium user
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([
            user_id,
            username,
            full_name,
            timestamp
        ])
        
        # Update premium user cache
        premium_user_cache[user_id] = True
        
        # Send notification to the user
        try:
            premium_notification = (
                "🎉 *Congratulations! You've been upgraded to Premium!*\n\n"
                "*New Premium Features:*\n"
                "✅ Search multiple land numbers at once\n"
                "✅ Get results with screenshots\n"
                "✅ Toggle between screenshot and text-only results\n\n"
                "Use /choose to toggle between screenshot and text-only results\n"
                "Use /help to see all available commands\n\n"
                "Thank you for supporting the bot! 🙏"
            )
            notification_msg = await context.bot.send_message(
                chat_id=int(user_id),
                text=premium_notification,
                parse_mode="Markdown"
            )
            # Store notification message ID in user_data
            if 'notification_messages' not in context.user_data:
                context.user_data['notification_messages'] = []
            context.user_data['notification_messages'].append(notification_msg.message_id)
        except Exception as e:
            logger.error(f"Error sending premium notification to user {user_id}: {str(e)}")
        
        # Delete processing message
        await processing_msg.delete()
        
        logger.info(f"Successfully added premium user: {display_name} (ID: {user_id})")
        await update.message.reply_text(f"✅ Successfully added {display_name} (ID: {user_id}) as premium user.")
        
    except Exception as e:
        logger.error(f"Error in add_premium - User ID: {user_id}, Error: {str(e)}")
        error_msg = await update.message.reply_text(f"❌ Error adding premium user: {str(e)}")
        error_messages[str(admin_id)] = error_msg

def is_premium_user(user_id: str) -> bool:
    global premium_user_cache
    
    # Check cache first
    if user_id in premium_user_cache:
        return premium_user_cache[user_id]
        
    try:
        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet(PREMIUM_USERS_TAB)
        premium_users = sheet.get_all_records()
        is_premium = any(str(user['user_id']) == str(user_id) for user in premium_users)
        
        # Cache the result
        premium_user_cache[user_id] = is_premium
        return is_premium
    except Exception as e:
        logger.error(f"Error checking premium status: {str(e)}")
        return False

async def remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    if not context.args or len(context.args) < 1:
        error_msg = await update.message.reply_text("⚠️ Usage: /removepremium <user_id>")
        error_messages[user_id] = error_msg
        return

    try:
        # Send processing message
        processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        user_id = context.args[0]
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        try:
            sheet = spreadsheet.worksheet(PREMIUM_USERS_TAB)
            
            # Find user row
            try:
                cell = sheet.find(user_id)
                if cell and cell.col == 1:  # Make sure we found the ID in the first column
                    # Get user info before deleting
                    row_data = sheet.row_values(cell.row)
                    username = row_data[1] if len(row_data) > 1 else 'Unknown'
                    full_name = row_data[2] if len(row_data) > 2 else 'Unknown'
                    display_name = username if username != 'Unknown' else full_name
                    
                    # Delete the row
                    sheet.delete_rows(cell.row)
                    
                    # Delete processing message
                    await processing_msg.delete()
                    
                    await update.message.reply_text(f"✅ Successfully removed {display_name} (ID: {user_id}) from premium users.")
                    logger.info(f"Removed premium user: {display_name} (ID: {user_id})")

                    # Send notification to the user
                    try:
                        removal_notification = (
                            "⚠️ *Premium Access Removed*\n\n"
                            "Your premium access has been removed\\. You will now have access to basic features only:\n\n"
                            "✅ Check land ownership information\n"
                            "✅ View land details\n"
                            "✅ Shown search results in text\n"
                            "✅ Search only one land number at once\n\n"
                            "Contact @Code404\\_SH if you believe this is a mistake\\."
                        )
                        notification_msg = await context.bot.send_message(
                            chat_id=int(user_id),
                            text=removal_notification,
                            parse_mode="MarkdownV2"
                        )
                        # Store notification message ID in user_data
                        if 'notification_messages' not in context.user_data:
                            context.user_data['notification_messages'] = []
                        context.user_data['notification_messages'].append(notification_msg.message_id)
                    except Exception as e:
                        logger.error(f"Error sending premium removal notification to user {user_id}: {str(e)}")
                else:
                    # Delete processing message
                    await processing_msg.delete()
                    error_msg = await update.message.reply_text(f"⚠️ User {user_id} is not a premium user.")
                    error_messages[user_id] = error_msg
            except Exception as e:
                # Delete processing message
                await processing_msg.delete()
                if "No matches found" in str(e):
                    error_msg = await update.message.reply_text(f"⚠️ User {user_id} is not a premium user.")
                    error_messages[user_id] = error_msg
                else:
                    logger.error(f"Error finding user in premium list: {str(e)}")
                    raise e
                
        except gspread.exceptions.WorksheetNotFound:
            # Delete processing message
            await processing_msg.delete()
            error_msg = await update.message.reply_text("❌ Premium users sheet does not exist.")
            error_messages[user_id] = error_msg
            return
        
    except Exception as e:
        # Delete processing message if it exists
        try:
            await processing_msg.delete()
        except:
            pass
        logger.error(f"Error removing premium user: {str(e)}")
        error_msg = await update.message.reply_text(f"❌ Error removing premium user: {str(e)}")
        error_messages[user_id] = error_msg

async def premium_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message and update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    try:
        # Send processing message
        if update.message:
            processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        else:
            processing_msg = await update.callback_query.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        client = get_gsheet_client()
        worksheet = client.open_by_key(SHEET_ID).worksheet(PREMIUM_USERS_TAB)
        data = worksheet.get_all_values()
        
        if len(data) <= 1:  # Only header row
            message_text = "No premium users found."
        else:
            message = "📋 <b>Premium Users List:</b>\n\n"
            for row in data[1:]:  # Skip header row
                user_id = row[0]
                username = row[1]
                full_name = row[2]
                added_date = row[3]  # Get the added_date
                
                message += f"👤 Name: {full_name}\n"
                message += f"🔹 Username: @{username}\n"
                message += f"🆔 ID: <code>{user_id}</code>\n"
                message += f"📅 Added: {added_date}\n"  # Add the date to display
                message += "➖➖➖➖➖➖➖➖\n"
            message_text = message
        
        # Delete processing message
        await processing_msg.delete()
        
        if update.message:
            await update.message.reply_text(message_text, parse_mode="HTML")
        else:
            await update.callback_query.message.reply_text(message_text, parse_mode="HTML")
            
        logging.debug(f"Premium users list displayed")
        
    except Exception as e:
        error_message = f"❌ Error getting premium users list: {str(e)}"
        logging.error(error_message)
        if update.message:
            error_msg = await update.message.reply_text(error_message)
            error_messages[user_id] = error_msg
        else:
            error_msg = await update.callback_query.message.reply_text(error_message)
            error_messages[user_id] = error_msg

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    await query.answer()  # Answer the callback query to remove the loading state
    
    if query.from_user.id != ADMIN_ID:
        error_msg = await query.message.reply_text("❌ You are not authorized to use admin commands.")
        error_messages[user_id] = error_msg
        return

    try:
        if query.data == "history":
            logging.debug("Admin requested history via button")
            await history(update, context)
        elif query.data == "broadcast":
            logging.debug("Admin requested broadcast via button")
            await query.message.reply_text("Please use /broadcast command directly to send a broadcast message.")
        elif query.data == "addpremium":
            logging.debug("Admin requested add premium via button")
            await query.message.reply_text("Please use /addpremium command followed by the user ID.")
        elif query.data == "premiumlist":
            logging.debug("Admin requested premium list via button")
            await premium_list(update, context)
        elif query.data == "removepremium":
            logging.debug("Admin requested remove premium via button")
            await query.message.reply_text("Please use /removepremium command followed by the user ID.")
        elif query.data == "status":
            logging.debug("Admin requested bot status via button")
            await bot_status(update, context)
    except Exception as e:
        logging.error(f"Error in button callback: {str(e)}")
        error_msg = await query.message.reply_text(f"❌ Error processing command: {str(e)}")
        error_messages[user_id] = error_msg

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use admin commands.")
        error_messages[user_id] = error_msg
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 History", callback_data="history"),
            InlineKeyboardButton("🤖 Bot Status", callback_data="status")
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="broadcast"),
            InlineKeyboardButton("📋 Premium List", callback_data="premiumlist")
        ],
        [
            InlineKeyboardButton("➕ Add Premium", callback_data="addpremium"),
            InlineKeyboardButton("➖ Remove Premium", callback_data="removepremium")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔰 Admin Panel:", reply_markup=reply_markup)

async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if user_id != str(ADMIN_ID):
        if update.callback_query:
            error_msg = await update.callback_query.message.reply_text("❌ You are not authorized to use this command.")
            error_messages[user_id] = error_msg
        else:
            error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
            error_messages[user_id] = error_msg
        return
    
    try:
        # Send processing message
        if update.callback_query:
            processing_msg = await update.callback_query.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        else:
            processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        # Get system info
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get bot uptime
        uptime = datetime.now() - start_time
        hours = uptime.total_seconds() // 3600
        minutes = (uptime.total_seconds() % 3600) // 60
        seconds = uptime.total_seconds() % 60
        
        # Get network info
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        
        # Get Python version
        python_version = platform.python_version()
        
        # Get OS info
        os_info = platform.platform()
        
        # Format message
        message = "🤖 <b>Bot Status Information</b>\n\n"
        
        message += "💻 <b>System Info:</b>\n"
        message += f"OS: {os_info}\n"
        message += f"Python: v{python_version}\n"
        message += f"Hostname: {hostname}\n"
        message += f"IP: {ip_address}\n\n"
        
        message += "⚙️ <b>Performance:</b>\n"
        message += f"CPU Usage: {cpu_percent}%\n"
        message += f"RAM Usage: {memory.percent}%\n"
        message += f"Free RAM: {memory.available / 1024 / 1024:.1f}MB\n"
        message += f"Total RAM: {memory.total / 1024 / 1024:.1f}MB\n"
        message += f"Disk Usage: {disk.percent}%\n"
        message += f"Free Disk: {disk.free / 1024 / 1024 / 1024:.1f}GB\n\n"
        
        message += "⏱ <b>Bot Stats:</b>\n"
        message += f"Uptime: {int(hours)}h {int(minutes)}m {int(seconds)}s\n"
        
        # Get number of users from User_Contacts
        client = get_gsheet_client()
        user_sheet = client.open_by_key(SHEET_ID).worksheet(USER_CONTACT_TAB)
        total_users = len(user_sheet.get_all_values()) - 1  # Subtract header row
        
        # Get number of premium users
        premium_sheet = client.open_by_key(SHEET_ID).worksheet(PREMIUM_USERS_TAB)
        premium_users = len(premium_sheet.get_all_values()) - 1  # Subtract header row
        
        message += f"Total Users: {total_users}\n"
        message += f"Premium Users: {premium_users}\n"
        
        # Get today's searches
        today = datetime.now().strftime('%Y-%m-%d')
        logs_sheet = client.open_by_key(SHEET_ID).worksheet("Full_Search_Logs")
        all_logs = logs_sheet.get_all_values()[1:]  # Skip header
        today_searches = sum(1 for row in all_logs if today in row[5])  # Assuming timestamp is in column 6
        
        message += f"Searches Today: {today_searches}\n"
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send message based on update type
        if update.callback_query:
            await update.callback_query.message.reply_text(message, parse_mode="HTML")
        else:
            await update.message.reply_text(message, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Error getting bot status: {str(e)}")
        error_message = f"❌ Error getting bot status: {str(e)}"
        if update.callback_query:
            error_msg = await update.callback_query.message.reply_text(error_message)
            error_messages[user_id] = error_msg
        else:
            error_msg = await update.message.reply_text(error_message)
            error_messages[user_id] = error_msg

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🏡 *MLMUPC Land Info Checker Bot*\n\n"
        "This bot helps you check land information from MLMUPC database\\.\n\n"
        "*How to use:*\n"
        "1️⃣ Simply send a land number in the format:\n"
        "`########\\-####`\n"
        "Example: `18020601\\-0001`\n\n"
        "*Normal Features:*\n"
        "✅ Check land ownership information\n"
        "✅ View land details\n"
        "✅ Shown search results in text\n"
        "✅ Search only one land number at once\n\n"
        "*Premium Features:*\n"
        "✅ Check land ownership information\n"
        "✅ View land details\n"
        "✅ Shown results as screenshot \\+ text\n"
        "✅ Search multiple land numbers at once\n"
        "✅ Toggle between screenshot and text\\-only results using /choose\n\n"
        "*Commands:*\n"
        "/start \\- Start the bot\n"
        "/help \\- Show this help message\n"
        "/id \\- Get your Telegram ID\n"
        "/choose \\- Toggle between screenshot and text\\-only results \\(Premium only\\)\n\n"
        "⚙️ More features will be added in the future\n\n"
        "*Contact Admin:*\n"
        "For premium access or support, contact:\n"
        "@Code404\\_SH\n\n"
        "Bot Developed with ❤️ by MNPT"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or update.message.from_user.full_name or "Unknown"
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    message = (
        "🆔 *Your Telegram ID*\n\n"
        f"Click to copy: `{user_id}`\n\n"
        "Send this ID to @Code404\\_SH to request premium access\\."
    )
    
    await update.message.reply_text(
        message,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )

def cleanup_screenshots():
    """Clean up any leftover screenshots in the screenshots directory"""
    try:
        screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots')
        if os.path.exists(screenshots_dir):
            for file in os.listdir(screenshots_dir):
                file_path = os.path.join(screenshots_dir, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {str(e)}")
    except Exception as e:
        logger.error(f"Error cleaning up screenshots directory: {str(e)}")

def start_screenshot_cleanup():
    """Start periodic screenshot cleanup"""
    while True:
        try:
            cleanup_screenshots()
            time.sleep(3600)  # Run every hour
        except Exception as e:
            logger.error(f"Error in screenshot cleanup thread: {str(e)}")
            time.sleep(60)

async def remove_error_message(user_id: str):
    """Remove the last error message for a user if it exists"""
    if user_id in error_messages and error_messages[user_id]:
        try:
            await error_messages[user_id].delete()
            error_messages[user_id] = None
        except Exception as e:
            logger.error(f"Error removing error message: {str(e)}")

def is_user_banned(user_id: str) -> bool:
    """Check if a user is banned"""
    global banned_users_cache
    
    # Check cache first
    if user_id in banned_users_cache:
        return banned_users_cache[user_id]
        
    try:
        client = get_gsheet_client()
        sheet = client.open_by_key(SHEET_ID).worksheet(BANNED_USERS_TAB)
        banned_users = sheet.get_all_records()
        is_banned = any(str(user['user_id']) == str(user_id) for user in banned_users)
        
        # Cache the result
        banned_users_cache[user_id] = is_banned
        return is_banned
    except Exception as e:
        logger.error(f"Error checking ban status: {str(e)}")
        return False

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user from using the bot"""
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    if not context.args or len(context.args) < 1:
        error_msg = await update.message.reply_text("⚠️ Usage: /ban <user_id> [reason]")
        error_messages[user_id] = error_msg
        return

    try:
        target_user_id = context.args[0]
        ban_reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Get user info from contacts
        try:
            contact_sheet = spreadsheet.worksheet(USER_CONTACT_TAB)
            user_data = contact_sheet.get_all_records()
            user_info = next((user for user in user_data if str(user['user_id']) == str(target_user_id)), None)
            
            if not user_info:
                error_msg = await update.message.reply_text(f"⚠️ User {target_user_id} not found in contacts.")
                error_messages[user_id] = error_msg
                return
                
            username = user_info.get('username', 'Unknown')
            full_name = user_info.get('full_name', 'Unknown')
            display_name = username if username != 'Unknown' else full_name
            
        except Exception as e:
            logger.error(f"Error accessing contacts for ban - User ID: {target_user_id}, Error: {str(e)}")
            error_msg = await update.message.reply_text("❌ Error accessing user contacts.")
            error_messages[user_id] = error_msg
            return
        
        # Handle banned users sheet
        try:
            sheet = spreadsheet.worksheet(BANNED_USERS_TAB)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=BANNED_USERS_TAB, rows=1000, cols=5)
            sheet.append_row(["user_id", "username", "full_name", "ban_reason", "banned_date"])
            logger.info(f"Created new {BANNED_USERS_TAB} worksheet for ban")
        
        # Check if already banned
        banned_users = sheet.get_all_records()
        if any(str(user['user_id']) == str(target_user_id) for user in banned_users):
            error_msg = await update.message.reply_text(f"⚠️ User {display_name} (ID: {target_user_id}) is already banned.")
            error_messages[user_id] = error_msg
            return

        # Add to banned users
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([
            target_user_id,
            username,
            full_name,
            ban_reason,
            timestamp
        ])
        
        # Update cache
        banned_users_cache[target_user_id] = True
        
        # Notify the banned user
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text=f"⚠️ You have been banned from using this bot.\nReason: {ban_reason}"
            )
        except Exception as e:
            logger.error(f"Error notifying banned user: {str(e)}")
        
        logger.info(f"Successfully banned user: {display_name} (ID: {target_user_id})")
        await update.message.reply_text(
            f"✅ Successfully banned {display_name} (ID: {target_user_id})\nReason: {ban_reason}"
        )
        
    except Exception as e:
        logger.error(f"Error in ban_user - User ID: {target_user_id}, Error: {str(e)}")
        error_msg = await update.message.reply_text(f"❌ Error banning user: {str(e)}")
        error_messages[user_id] = error_msg

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user from using the bot"""
    user_id = str(update.message.from_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    if not context.args or len(context.args) < 1:
        error_msg = await update.message.reply_text("⚠️ Usage: /unban <user_id>")
        error_messages[user_id] = error_msg
        return

    try:
        target_user_id = context.args[0]
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        try:
            sheet = spreadsheet.worksheet(BANNED_USERS_TAB)
            
            # Find user row
            try:
                cell = sheet.find(target_user_id)
                if cell and cell.col == 1:  # Make sure we found the ID in the first column
                    # Get user info before deleting
                    row_data = sheet.row_values(cell.row)
                    username = row_data[1] if len(row_data) > 1 else 'Unknown'
                    full_name = row_data[2] if len(row_data) > 2 else 'Unknown'
                    display_name = username if username != 'Unknown' else full_name
                    
                    # Delete the row using delete_rows
                    sheet.delete_rows(cell.row)
                    
                    # Update cache
                    banned_users_cache[target_user_id] = False
                    
                    # Notify the unbanned user
                    try:
                        await context.bot.send_message(
                            chat_id=int(target_user_id),
                            text="✅ You have been unbanned from using this bot."
                        )
                    except Exception as e:
                        logger.error(f"Error notifying unbanned user: {str(e)}")
                    
                    await update.message.reply_text(f"✅ Successfully unbanned {display_name} (ID: {target_user_id})")
                    logger.info(f"Unbanned user: {display_name} (ID: {target_user_id})")
                else:
                    error_msg = await update.message.reply_text(f"⚠️ User {target_user_id} is not banned.")
                    error_messages[user_id] = error_msg
            except Exception as e:
                if "No matches found" in str(e):
                    error_msg = await update.message.reply_text(f"⚠️ User {target_user_id} is not banned.")
                    error_messages[user_id] = error_msg
                else:
                    raise e
                
        except gspread.exceptions.WorksheetNotFound:
            error_msg = await update.message.reply_text("❌ Banned users sheet does not exist.")
            error_messages[user_id] = error_msg
            return
        
    except Exception as e:
        logger.error(f"Error unbanning user: {str(e)}")
        error_msg = await update.message.reply_text(f"❌ Error unbanning user: {str(e)}")
        error_messages[user_id] = error_msg

async def ban_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of banned users"""
    user_id = str(update.effective_user.id)
    
    # Remove any existing error message
    await remove_error_message(user_id)
    
    if update.message and update.message.from_user.id != ADMIN_ID:
        error_msg = await update.message.reply_text("❌ You are not authorized to use this command.")
        error_messages[user_id] = error_msg
        return

    try:
        # Send processing message
        if update.message:
            processing_msg = await update.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        else:
            processing_msg = await update.callback_query.message.reply_text("_🤖 Processing data..._", parse_mode="Markdown", disable_notification=True)
        
        client = get_gsheet_client()
        worksheet = client.open_by_key(SHEET_ID).worksheet(BANNED_USERS_TAB)
        data = worksheet.get_all_values()
        
        if len(data) <= 1:  # Only header row
            message_text = "No banned users found."
        else:
            message = "📋 <b>Banned Users List:</b>\n\n"
            for row in data[1:]:  # Skip header row
                user_id = row[0]
                username = row[1]
                full_name = row[2]
                ban_reason = row[3]
                banned_date = row[4]
                
                message += f"👤 Name: {full_name}\n"
                message += f"🔹 Username: @{username}\n"
                message += f"🆔 ID: <code>{user_id}</code>\n"
                message += f"⚠️ Reason: {ban_reason}\n"
                message += f"📅 Banned: {banned_date}\n"
                message += "➖➖➖➖➖➖➖➖\n"
            message_text = message
        
        # Delete processing message
        await processing_msg.delete()
        
        if update.message:
            await update.message.reply_text(message_text, parse_mode="HTML")
        else:
            await update.callback_query.message.reply_text(message_text, parse_mode="HTML")
            
        logging.debug(f"Banned users list displayed")
        
    except Exception as e:
        error_message = f"❌ Error getting banned users list: {str(e)}"
        logging.error(error_message)
        if update.message:
            error_msg = await update.message.reply_text(error_message)
            error_messages[user_id] = error_msg
        else:
            error_msg = await update.callback_query.message.reply_text(error_message)
            error_messages[user_id] = error_msg

def ensure_banned_users_sheet_exists():
    """Ensure banned users sheet exists with correct headers"""
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Check if Banned_Users worksheet exists
        try:
            sheet = spreadsheet.worksheet(BANNED_USERS_TAB)
            # Check headers
            headers = sheet.row_values(1)
            expected_headers = ["user_id", "username", "full_name", "ban_reason", "banned_date"]
            
            # If headers don't match, update them
            if headers != expected_headers:
                sheet.clear()  # Clear the sheet
                sheet.append_row(expected_headers)  # Add correct headers
                logger.info(f"Updated {BANNED_USERS_TAB} headers")
                
        except gspread.exceptions.WorksheetNotFound:
            # Create new worksheet with correct headers
            sheet = spreadsheet.add_worksheet(title=BANNED_USERS_TAB, rows=1000, cols=5)
            sheet.append_row(["user_id", "username", "full_name", "ban_reason", "banned_date"])
            logger.info(f"Created new {BANNED_USERS_TAB} worksheet")
            
        return sheet
            
    except Exception as e:
        logger.error(f"Error ensuring banned users sheet exists: {str(e)}")
        return None

# === MAIN RUN ===
if __name__ == "__main__":
    logger.info("=== Starting Telegram Land Checker Bot ===")
    load_user_database()
    
    # Ensure required sheets exist
    ensure_premium_sheet_exists()
    ensure_banned_users_sheet_exists()

    # Only run Flask server locally (not in Railway production)
    if os.getenv("RAILWAY_ENVIRONMENT") != "production":
        threading.Thread(target=run_flask).start()
    
    threading.Thread(target=auto_ping).start()
    threading.Thread(target=auto_upload_logs).start()  # Start log upload thread
    threading.Thread(target=start_screenshot_cleanup).start()  # Start screenshot cleanup thread

    # === DEBUG INFO ===
    logger.info(f"Current directory: {os.getcwd()}")
    logger.info(f"Files in directory: {os.listdir()}")
    logger.info(f"Bot Token Status: {'✅ Loaded bot token' if os.getenv('BOT_TOKEN') else '❌ Bot token Not found'}")

    # === BOT INITIALIZATION ===
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.critical("❌ BOT_TOKEN not found in environment variables!")
        raise ValueError("❌ BOT_TOKEN not found in environment variables!")

    app_bot = ApplicationBuilder()\
        .token(token)\
        .connection_pool_size(1)\
        .get_updates_connection_pool_size(1)\
        .concurrent_updates(False)\
        .build()
    
    logger.info("Adding command handlers...")
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("admin", admin))
    app_bot.add_handler(CommandHandler("history", history))
    app_bot.add_handler(CommandHandler("broadcast", broadcast))
    app_bot.add_handler(CommandHandler("addpremium", add_premium))
    app_bot.add_handler(CommandHandler("removepremium", remove_premium))
    app_bot.add_handler(CommandHandler("premiumlist", premium_list))
    app_bot.add_handler(CommandHandler("status", bot_status))
    app_bot.add_handler(CommandHandler("id", get_id))
    app_bot.add_handler(CommandHandler("choose", choose_preference))
    app_bot.add_handler(CommandHandler("ban", ban_user))
    app_bot.add_handler(CommandHandler("unban", unban_user))
    app_bot.add_handler(CommandHandler("banlist", ban_list))
    app_bot.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_multiple_land_numbers))
    app_bot.add_handler(CallbackQueryHandler(handle_preference_callback))  # Remove pattern to catch all callbacks
    app_bot.add_handler(CallbackQueryHandler(button_callback))  # Move this after the preference handler
    
    # Use webhook on Railway, polling locally
    if os.getenv("RAILWAY_ENVIRONMENT") == "production":
        logger.info("Starting bot in production mode with webhook")
        app_bot.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 8080)),
            webhook_url=os.getenv("WEBHOOK_URL")
        )
    else:
        logger.info("Starting bot in local mode with polling")
        while True:
            try:
                app_bot.run_polling(drop_pending_updates=True)
            except telegram.error.NetworkError as e:
                logger.error(f"Network error occurred: {str(e)}")
                logger.info("Attempting to reconnect in 5 seconds...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error occurred: {str(e)}")
                logger.info("Attempting to reconnect in 5 seconds...")
                time.sleep(5)