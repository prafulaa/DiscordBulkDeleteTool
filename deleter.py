from api_client import DiscordClient
from utils import print_info, print_success, print_warning, print_error, logger, get_snowflake_time
import time
import random
from tqdm import tqdm

class MessageDeleter:
    def __init__(self, client: DiscordClient):
        self.client = client

    def scan_messages(self, context_id, is_dm=False, author_id=None, content_query=None, progress_callback=None):
        """
        Scans for messages matching criteria.
        context_id: guild_id if server, channel_id if DM.
        """
        all_messages = []
        offset = 0
        total_results = 0
        
        print_info("Scanning for messages... (This relies on Discord Search API)")
        
        while True:
            # For servers we pass guild_id, for DMs we pass channel_id
            guild_id = context_id if not is_dm else None
            channel_id = context_id if is_dm else None
            
            # Author ID defaults to self if not provided (safety default)
            target_author = author_id if author_id else self.client.user_id
            
            data = self.client.search_messages(
                guild_id=guild_id, 
                channel_id=channel_id, 
                author_id=target_author,
                content=content_query,
                offset=offset
            )
            
            if not data:
                break
                
            messages = data.get('messages', [])
            total_results = data.get('total_results', 0)
            
            if not messages:
                break
                
            # 'messages' in search result is a list of lists (conversations). 
            # We flatten and filter for our target message.
            for group in messages:
                for msg in group:
                    # Double check author to be sure
                    if msg['author']['id'] == target_author:
                        # Append minimal data needed
                        all_messages.append({
                            'id': msg['id'],
                            'channel_id': msg['channel_id'],
                            'content': msg['content'],
                            'timestamp': msg['timestamp'],
                            'attachments': len(msg['attachments']) > 0
                        })
            
            print_info(f"Found {len(all_messages)} messages so far...")
            
            if progress_callback:
                # Send the batch of newly found messages to the GUI
                # extracting just the new ones from this batch
                new_batch = []
                for group in messages:
                     for msg in group:
                        if msg['author']['id'] == target_author:
                             new_batch.append({
                                'id': msg['id'],
                                'channel_id': msg['channel_id'],
                                'content': msg['content'],
                                'timestamp': msg['timestamp'],
                                'attachments': len(msg['attachments']) > 0
                             })
                if new_batch:
                    progress_callback(new_batch)
            
            offset += 25
            if offset >= total_results:
                break
            
            # Search API has strict rate limits, take it slow
            time.sleep(1.5) 
            
        print_success(f"Scan complete. Found {len(all_messages)} total messages matches.")
        return all_messages

    def execute_deletion(self, messages, dry_run=False, progress_callback=None, skip_confirm=False):
        """
        Deletes the provided list of messages.
        """
        if not messages:
            print_warning("No messages to delete.")
            return

        print_info(f"Preparing to delete {len(messages)} messages...")
        print_info(f"dry_run={dry_run}, skip_confirm={skip_confirm}")  # DEBUG
        
        if dry_run:
            print_info("DRY RUN MODE: No messages will be deleted.")
            for msg in messages[:5]: # Show first 5
                print(f"[DRY RUN] Would delete: {msg['content'][:50]}... (ID: {msg['id']})")
            return

        if not skip_confirm:
            confirm = input(f"Are you SURE you want to delete {len(messages)} messages? This cannot be undone. (y/N): ")
            if confirm.lower() != 'y':
                print_warning("Deletion cancelled.")
                return

        deleted_count = 0
        failed_count = 0
        
        # Determine delay based on quantity to stay safe
        # More messages = slower per message to avoid pattern detection
        base_delay = 1.2 
        
        print_info(f"Starting deletion of {len(messages)} messages...")  # DEBUG
        
        for i, msg in enumerate(messages):
            print_info(f"Deleting message {i+1}/{len(messages)}: {msg['id']} in channel {msg['channel_id']}")  # DEBUG
            success = self.client.delete_message(msg['channel_id'], msg['id'])
            
            if success:
                deleted_count += 1
                logger.info(f"Deleted message {msg['id']} in channel {msg['channel_id']}")
                print_info(f"  -> SUCCESS")  # DEBUG
            else:
                failed_count += 1
                print_warning(f"  -> FAILED")  # DEBUG
            
            if progress_callback:
                progress_callback(deleted_count, failed_count, len(messages))
            
            # Variable delay to look human
            time.sleep(base_delay + random.uniform(0.1, 0.8))
        
        print_success(f"Deletion complete.")
        print_info(f"Deleted: {deleted_count}")
        print_info(f"Failed: {failed_count}")
