import sys
from auth import get_user_token
from api_client import DiscordClient
from deleter import MessageDeleter
from utils import print_info, print_warning, print_error, print_success, parse_date, logger

def main():
    print_info("=== Discord Bulk Message Tool ===")
    print_warning("SAFETY NOTICE: Automating user accounts is against Discord TOS.")
    print_warning("Use this tool at your own risk. Delays are implemented for safety.")
    print("")
    
    # 1. Auth
    token = get_user_token()
    if not token:
        return
        
    client = DiscordClient(token)
    user_info = client.validate_token()
    
    if not user_info:
        print_error("Invalid token. Login failed.")
        return
        
    print_success(f"Logged in as {user_info['username']}#{user_info['discriminator']}")
    
    # 2. Configuration
    while True:
        print("\n--- Menu ---")
        print("1. Delete messages from a DM (Direct Message)")
        print("2. Delete messages from a specific Server (Guild)")
        print("3. Exit")
        
        choice = input("Select an option (1-3): ").strip()
        
        if choice == '3':
            print("Exiting.")
            break
            
        context_id = input("Enter the ID (Channel ID for DM, Server ID for Server): ").strip()
        if not context_id.isdigit():
            print_error("Invalid ID format. Must be numeric.")
            continue
            
        content_query = input("Optional: Filter by keyword (press Enter to skip): ").strip()
        if not content_query: content_query = None
        
        # 3. Execution
        deleter = MessageDeleter(client)
        is_dm = (choice == '1')
        
        try:
            messages = deleter.scan_messages(
                context_id=context_id, 
                is_dm=is_dm, 
                content_query=content_query
            )
            
            if not messages:
                print_info("No messages found matching criteria.")
                continue
                
            deleter.execute_deletion(messages)
            
        except KeyboardInterrupt:
            print_warning("\nOperation cancelled by user.")
        except Exception as e:
            print_error(f"An unexpected error occurred: {e}")
            logger.error(f"Main loop error: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
