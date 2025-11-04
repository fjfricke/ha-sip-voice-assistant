"""Home Assistant REST API client."""
import aiohttp
from typing import Dict, Any, Optional
from app.config import Config


class HomeAssistantClient:
    """Client for calling Home Assistant services."""
    
    def __init__(self, config: Config):
        ha_config = config.get_homeassistant_config()
        self.url = ha_config["url"].rstrip('/')
        self.token = ha_config["token"]
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Debug: Check token (but don't print full token for security)
        if not self.token:
            print("⚠️  WARNING: HOMEASSISTANT_TOKEN is empty or not set!")
        else:
            token_preview = self.token[:10] + "..." if len(self.token) > 10 else "***"
            print(f"✅ Home Assistant configured: URL={self.url}, Token={token_preview}")
    
    async def start(self):
        """Start the HTTP client session."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self.session = aiohttp.ClientSession(headers=headers)
    
    async def stop(self):
        """Stop the HTTP client session."""
        if self.session:
            await self.session.close()
    
    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Call a Home Assistant service.
        
        Args:
            domain: Service domain (e.g., "light")
            service: Service name (e.g., "turn_on")
            entity_id: Optional entity ID to target
            **kwargs: Additional service data
        
        Returns:
            Response from Home Assistant API
        """
        if not self.session:
            await self.start()
        
        url = f"{self.url}/services/{domain}/{service}"
        
        service_data = kwargs.copy()
        if entity_id:
            service_data["entity_id"] = entity_id
        
        print(f"🔧 Calling HA service: {url}")
        print(f"🔧 Service data: {service_data}")
        print(f"🔧 Authorization header present: {bool(self.token)}")
        
        async with self.session.post(url, json=service_data) as response:
            if response.status == 401:
                error_text = await response.text()
                print("❌ HA Authentication failed (401)")
                print(f"   URL: {url}")
                print(f"   Token present: {bool(self.token)}")
                print(f"   Token length: {len(self.token) if self.token else 0}")
                print(f"   Response: {error_text[:200]}")
                response.raise_for_status()
            
            if response.status == 400:
                error_text = await response.text()
                print("❌ HA Bad Request (400)")
                print(f"   URL: {url}")
                print(f"   Service data: {service_data}")
                print(f"   Response: {error_text[:500]}")
            
            response.raise_for_status()
            return await response.json()
    
    async def get_state(self, entity_id: str) -> Dict[str, Any]:
        """
        Get the state of an entity.
        
        Args:
            entity_id: The entity ID to query
        
        Returns:
            Entity state information
        """
        if not self.session:
            await self.start()
        
        url = f"{self.url}/states/{entity_id}"
        
        async with self.session.get(url) as response:
            response.raise_for_status()
            return await response.json()
    
    async def search_entities(self, domain: Optional[str] = None) -> list[Dict[str, Any]]:
        """
        Search for entities, optionally filtered by domain.
        
        Args:
            domain: Optional domain to filter by
        
        Returns:
            List of entity states
        """
        if not self.session:
            await self.start()
        
        url = f"{self.url}/states"
        
        async with self.session.get(url) as response:
            response.raise_for_status()
            states = await response.json()
            if domain:
                return [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
            else:
                return states

