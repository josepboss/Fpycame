# AI Rules for FunPay Cardinal

## Tech Stack
*   **Python 3.11+**: The core programming language used for the application logic and bot functionality.
*   **pyTelegramBotAPI (telebot)**: The primary library for interacting with the Telegram Bot API, handling commands, and building the Control Panel (CP).
*   **Requests**: Used for all synchronous HTTP communication with the FunPay website and external APIs.
*   **BeautifulSoup4 (bs4) with lxml**: The standard toolset for parsing and extracting data from FunPay's HTML responses.
*   **Configparser**: Handles configuration management using `.cfg` files for main settings, auto-delivery, and auto-response.
*   **Bcrypt**: Used for secure hashing and verification of the Telegram access password.
*   **Logging**: A structured logging system configured to output to both the CLI (with colors) and rotating log files.
*   **Colorama**: Provides cross-platform colored terminal output for better CLI readability.

## Library Usage Rules

### 1. Web Interaction & Scraping
*   **Use `requests`** for all network requests. Do not use `urllib` or other HTTP clients unless specifically required for compatibility.
*   **Use `BeautifulSoup`** for parsing HTML. Always specify the `lxml` parser for performance and consistency.
*   **Data Extraction**: Prefer using CSS selectors or specific attribute filters in BeautifulSoup to keep scraping logic robust against minor UI changes.

### 2. Telegram Bot Interface
*   **Use `pyTelegramBotAPI`** for all bot interactions.
*   **Keyboards**: Use `InlineKeyboardMarkup` and `InlineKeyboardButton` for the Control Panel. Follow the existing pattern in `tg_bot/keyboards.py` and `tg_bot/static_keyboards.py`.
*   **States**: Use the custom state management system in `TGBot` (via `set_state`, `get_state`, `clear_state`) for multi-step user interactions.

### 3. Configuration & Storage
*   **Use `configparser`** for all settings stored in the `configs/` directory.
*   **File Paths**: Always use relative paths or `os.path.join` to ensure cross-platform compatibility (Windows/Linux).
*   **JSON**: Use the `json` library for caching temporary data in `storage/cache/`.

### 4. Localization
*   **Mandatory Localization**: Never hardcode user-facing strings. Always use the `Localizer` class (`_("variable_name")`) to support Russian, English, and Ukrainian.
*   **Adding Strings**: New strings must be added to `locales/ru.py`, `locales/en.py`, and `locales/uk.py`.

### 5. Concurrency
*   **Threading**: Use the `threading.Thread` class for background tasks (like the event loop, auto-raise loop, and sending notifications) to keep the main bot process responsive.

### 6. Utilities & Best Practices
*   **Logging**: Use `logging.getLogger("FPC.submodule")` for all event tracking. Avoid using `print()`.
*   **Tools**: Check `Utils/cardinal_tools.py` before implementing common logic (e.g., text formatting, proxy validation, time conversion) to avoid duplication.
*   **Type Hinting**: Always use type hints for function arguments and return values to improve code clarity and IDE support.