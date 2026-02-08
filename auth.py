import getpass
from utils import print_info, print_success, print_error, logger

def get_user_token():
    """
    Prompt the user to input their Discord Token securely.
    """
    print_info("Please enter your Discord User Token.")
    print_info("To find this: Open Discord > Ctrl+Shift+I > Network Tab > Filter 'api' > Refresh > Click a Request > Look for 'Authorization' header.")
    
    # Priority 1: Check for token.txt
    import os
    token_file = os.path.join(os.path.dirname(__file__), 'token.txt')
    if os.path.exists(token_file):
        try:
            with open(token_file, 'r') as f:
                token = f.read().strip()
                if token:
                    print_info("Loaded token from token.txt")
                    return token
        except Exception as e:
            logger.error(f"Failed to read token.txt: {e}")

    # Priority 2: Check Environment Variable
    env_token = os.environ.get("DISCORD_TOKEN")
    if env_token:
        print_info("Loaded token from environment variable.")
        return env_token

    # Priority 3: Prompt User
    token = getpass.getpass(prompt="Token: ").strip()
    
    if not token:
        print_error("Token cannot be empty.")
        return None
    
    # Basic format check (just to catch obvious errors)
    if len(token) < 20: 
        print_error("Token seems too short. Please check again.")
        return None
        
    return token
