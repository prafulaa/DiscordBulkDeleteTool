import requests
import time
import random
from utils import logger, print_warning, print_error

class DiscordClient:
    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.base_url = "https://discord.com/api/v9"
        self.user_id = None

    def validate_token(self):
        """
        Validates the token by fetching user info.
        Returns user info dict if valid, None otherwise.
        """
        response = self._request("GET", "/users/@me")
        if response and response.status_code == 200:
            data = response.json()
            self.user_id = data['id']
            return data
        return None

    def _request(self, method, endpoint, params=None, json_data=None):
        """
        Internal request wrapper with Rate Limit handling.
        """
        url = f"{self.base_url}{endpoint}"
        retries = 3
        
        for i in range(retries):
            try:
                response = requests.request(
                    method, 
                    url, 
                    headers=self.headers, 
                    params=params, 
                    json=json_data
                )
                
                # Check for Rate Limit
                if response.status_code == 429:
                    retry_after = response.json().get('retry_after', 1)
                    print_warning(f"Rate limited. Sleeping for {retry_after} seconds...")
                    time.sleep(retry_after + 0.5) # Add a small buffer
                    continue
                
                # Check for Unauthorized
                if response.status_code == 401:
                    print_error("Unauthorized: Invalid Token.")
                    return None
                    
                return response
                
            except requests.RequestException as e:
                print_error(f"Request failed: {e}")
                time.sleep(2) # Wait before retry on network error
                
        return None

    def search_messages(self, guild_id=None, channel_id=None, author_id=None, content=None, min_id=None, max_id=None, offset=0):
        """
        Search for messages using Discord's search API.
        This is much more efficient than iterating history for specific users.
        """
        params = {}
        if author_id:
            params['author_id'] = author_id
        if content:
            params['content'] = content
        if min_id:
            params['min_id'] = min_id
        if max_id:
            params['max_id'] = max_id
        params['offset'] = offset
        
        # Determine strict endpoint
        if guild_id:
            endpoint = f"/guilds/{guild_id}/messages/search"
        elif channel_id:
            endpoint = f"/channels/{channel_id}/messages/search"
        else:
             # Global DM search not easily supported without iterating all channels, 
             # so we force channel_id for DMs or guild_id for servers.
            print_error("Must specify guild_id or channel_id for search.")
            return None

        response = self._request("GET", endpoint, params=params)
        if response and response.status_code == 200:
            return response.json()
        return None

    def delete_message(self, channel_id, message_id):
        """
        Deletes a single message.
        """
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        response = self._request("DELETE", endpoint)
        
        # Handle None response (network error or retries exhausted)
        if response is None:
            logger.error(f"Failed to delete {message_id}: No response from server")
            return False
        
        if response.status_code == 204:
            return True
        elif response.status_code == 403:
             # Often means message is too old or missing perms (though for own messages in DM, 403 usually means something else)
             # actually 403 on own message delete usually means "Cannot delete this message" (e.g. system message)
             # OR strictly for bots trying to delete >2 weeks old messages.
             # For USERS deleting OWN messages, 204 is expected unless rate limited.
            logger.warning(f"Failed to delete {message_id}: 403 Forbidden")
            return False
        elif response.status_code == 404:
            logger.warning(f"Message {message_id} not found/already deleted")
            return True # Treat as success
        else:
            logger.error(f"Failed to delete {message_id}: Status {response.status_code}")
            return False
