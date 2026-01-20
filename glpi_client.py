import requests
import streamlit as st

class GlpiApi:
    def __init__(self, api_url, app_token, user_token=None, username=None, password=None):
        self.api_url = api_url
        self.app_token = app_token
        self.user_token = user_token
        self.username = username
        self.password = password
        self.session_token = None

    def _get_auth_headers(self):
        headers = {
            "Content-Type": "application/json",
            "App-Token": self.app_token,
        }
        if self.user_token:
            headers["Authorization"] = f"user_token {self.user_token}"
        return headers

    def init_session(self):
        """Initializes a session with GLPI and returns a session token."""
        headers = self._get_auth_headers()
        
        try:
            response = requests.get(f"{self.api_url}/initSession", headers=headers)
            response.raise_for_status()
            self.session_token = response.json()["session_token"]
            return True, None
        except requests.exceptions.RequestException as e:
            error_message = f"Error initializing GLPI session: {e}"
            if e.response is not None:
                error_message += f"\nResponse: {e.response.text}"
            return False, error_message

    def get_computers(self):
        """Fetches all computers from GLPI, ensuring all required fields are included."""
        if not self.session_token:
            success, error = self.init_session()
            if not success:
                return None, error

        headers = {
            "Content-Type": "application/json",
            "App-Token": self.app_token,
            "Session-Token": self.session_token,
        }
        
        # Define all the fields required by the application to ensure they are fetched.
        required_fields = "id,otherserial,name,computermodels_id,serial,computertypes_id,states_id,users_id,manufacturers_id,date_mod,date_creation,locations_id,comment"
        
        computers = []
        page_size = 1000 # A common page size
        total_count = None

        try:
            # First, get the total count of items.
            # The 'fields' parameter is not strictly necessary here, but good practice.
            url = f"{self.api_url}/Computer?range=0-0&expand_dropdowns=true&get_hateoas=false&fields={required_fields}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            content_range = response.headers.get("Content-Range")
            if content_range:
                try:
                    # Expected format is "items 0-0/123" or just "123"
                    total_count = int(content_range.split('/')[-1])
                except (ValueError, IndexError):
                    pass # Could not parse total_count, will use fallback

            if total_count is not None:
                # Efficiently fetch all items using the total_count
                for range_start in range(0, total_count, page_size):
                    url = f"{self.api_url}/Computer?range={range_start}-{range_start + page_size - 1}&expand_dropdowns=true&get_hateoas=false&fields={required_fields}"
                    response = requests.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    if data:
                        computers.extend(data)
            else:
                # Fallback to the old method if Content-Range is not available or unparsable
                range_start = 0
                while True:
                    url = f"{self.api_url}/Computer?range={range_start}-{range_start + page_size - 1}&expand_dropdowns=true&get_hateoas=false&fields={required_fields}"
                    response = requests.get(url, headers=headers)

                    if response.status_code == 400 and "ERROR_RANGE_EXCEED_TOTAL" in response.text:
                        break # Stop when the range goes beyond the total items
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    if not data:
                        break # Stop if the API returns an empty list
                    
                    computers.extend(data)
                    
                    if len(data) < page_size:
                        break # Stop if the last page had fewer items than page_size
                        
                    range_start += page_size

            return computers, None
        except requests.exceptions.RequestException as e:
            error_message = f"Error fetching computers: {e}"
            if e.response is not None:
                error_message += f"\nResponse: {e.response.text}"
            return None, error_message

    def kill_session(self):
        """Kills the current GLPI session."""
        if not self.session_token:
            return

        headers = {
            "Content-Type": "application/json",
            "App-Token": self.app_token,
            "Session-Token": self.session_token,
        }

        try:
            requests.get(f"{self.api_url}/killSession", headers=headers)
        except requests.exceptions.RequestException:
            # Ignore errors on kill session
            pass
        finally:
            self.session_token = None
