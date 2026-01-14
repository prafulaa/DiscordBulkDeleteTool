# Discord Bulk Message Deletion Tool

A Python tool to bulk delete your **own** messages from Discord DMs or Servers.

> [!WARNING]
> **USE AT YOUR OWN RISK**
> Automating user actions ("Self-Botting") is technically a violation of Discord's Terms of Service.
> This tool implements safety delays to minimize risk, but you are responsible for your account's safety.

## Features
- **Filter Support**: Delete messages based on keywords.
- **Context Support**: Works in DMs (Direct Messages) and Servers.
- **Safety First**: Implements specialized delays to mimic human behavior and avoid rate limits.
- **Logging**: Keeps a record of deleted messages in `discord_tool.log`.

## Setup

1. **Install Python**: Ensure Python 3.8+ is installed.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## How to Get Your Token
To use this tool, you need your User Token. **Do not share this token with anyone.**

1. Open Discord in your browser or Desktop app.
2. Press `Ctrl + Shift + I` inside Discord to open Developer Tools.
3. Go to the **Network** tab.
4. Type `api` in the filter box.
5. Refresh Discord (`Ctrl + R`).
6. Click on any request (like `library` or `messages`) in the list.
7. In the **Headers** tab on the right, scroll down to **Request Headers**.
8. Copy the value of the `authorization` header (it looks like a long random string).

## Usage

1. Run the script:
   ```bash
   python main.py
   ```
2. Paste your token when prompted (password input remains hidden).
3. Select whether you want to clean a DM or a Server.
4. Provide the ID:
   - **For DM**: Right-click the DM -> Copy Channel ID (Enable Developer Mode in Discord Settings > Advanced to see this).
   - **For Server**: Right-click the Server Icon -> Copy Server ID.
5. (Optional) Enter a keyword to only delete messages containing that word.
6. Review the count and confirm deletion.

## Troubleshooting
- **401 Unauthorized**: Your token is wrong/expired. Get a fresh one.
- **403 Forbidden**: You are trying to delete someone else's message, or a system message. The tool skips these.
- **429 Too Many Requests**: You use the tool too fast. The tool handles this automatically by sleeping, but if it happens often, stop using it for a few hours.
