"""
Environmental engine module

Responsible for managing the state and behaviour of the simulation environment
"""

# Import from core directory
from core.event_manager import EventManager
from config.environment_config import EnvironmentConfig
# Note: Other modules may need to create or modify import paths
# Temporaryly removes missing imports so that basic codes can be run
# from core.action_manager import ActionManager
# Replace with a simple class definition
class ActionManager:
    def __init__(self):
        pass
    def execute_action(self, *args, **kwargs):
        pass
    def reset(self):
        pass

class StatusManager:
    def __init__(self):
        pass
    def reset(self):
        pass

class EnterpriseNetworkManager:
    def __init__(self):
        self.enterprises = {}
    def reset(self):
        self.enterprises = {}
    def initialize_network(self, *args, **kwargs):
        pass
    def get_network_state(self):
        return {}



class Environment:
    """
    Simulation Environment Class
    Management of the state and behaviour of the simulation environment as a controlled component of Controller
        """
    def __init__(self):
        """
        Initializing Environmental Engines
                """
        self.action_manager = None
        self.network_manager = None
        self.event_manager = None
        self.state_manager = None  # Save Status Manager Reference
        self.message_manager = None  
        self.time_manager = None
        self.controller = None  # controller reference
        
        # State of Environment
        self.enterprises = {}
        self.state_history = []
        self.event_history = []
    
    def set_time_manager(self, time_manager):
        """
        Set Timestep Manager Example
                
        Args:
            time_manager: Timestep manager example
                """
        self.time_manager = time_manager
        
    def set_network_manager(self, network_manager):
        """
        Set example of a network manager
                
        Args:
            parameter: Examples of a network manager
                """
        self.network_manager = network_manager
        
    def set_message_manager(self, message_manager):
        """
        Set Message Manager Example
                
        Args:
            parameter: Message Manager Example
                """
        self.message_manager = message_manager
        
    def set_controller(self, controller):
        """
        Set controller instance
                
        Args:
            Controller: instance of Controller
                """
        self.controller = controller
        # Ensure that the environment is synchronized with the event manager of the controller
        if hasattr(controller, 'event_manager') and controller.event_manager:
            self.event_manager = controller.event_manager
        # Ensure that the environment is synchronized with the state manager of the controller
        if hasattr(controller, 'state_manager') and controller.state_manager:
            self.state_manager = controller.state_manager
        if hasattr(controller, 'network_manager') and controller.network_manager:
            self.network_manager = controller.network_manager
        if hasattr(controller, 'action_manager') and controller.action_manager:
            self.action_manager = controller.action_manager
        if hasattr(controller, 'message_manager') and controller.message_manager:
            self.message_manager = controller.message_manager
        if hasattr(controller, 'time_manager') and controller.time_manager:
            self.time_manager = controller.time_manager
    
    def reset(self, network_config=None, enterprise_configs=None):
        """
        Reset Environment
                
        Args:
            parameter: Network Configuration
            enterprise_configs: enterprise Configuration List
                """
        # Reset all managers - Add check to avoid empty references
        if hasattr(self, 'network_manager') and self.network_manager:
            self.network_manager.reset()
        else:
            print("Warning: enterprise Network Manager not set, Skip Reset")
        
        if hasattr(self, 'event_manager') and self.event_manager:
            self.event_manager.reset()
        else:
            print("Warning: Event Manager not set, Skip Reset")
        
        
        # Initialization State
        self.state_history = []
        self.event_history = []
        
        # Save Initial Status
        initial_state = self.get_current_state()
        self.state_history.append(initial_state)
    
    def step(self, step: int):
        """
        Execute a step-by-step environmental update - handle overall simulated events
                
        Responsible for system-level events such as market changes, supply disruptions, seasonal fluctuations, etc.
                
        Args:
            step: Current time step    
                
        Returns:
            dict: Updated state of the environment and outcome of events
                """
        
        # Can not open message
        events = self.event_manager.get_next_events(self.time_manager.get_datetime())
        
        event_processing_results = {}
        
        # Handle the acquired events
        if events:
            print(f"Processing {len(events)} environment events...")
            # Get the current status for event handling
            current_state = self.get_current_state()
            
            # Dealing with different events by type
            for event in events:
                event_type = event.get('type')
                
                # Example of specific event handling
                if event_type == 'MARKET_DEMAND_CHANGE':
                    print(f"Processing market-demand event: {event.get('data', {})}")
                    # Update the level of demand in market conditions
                    self._update_market_demand(event.get('data', {}))
                    
                elif event_type == 'SUPPLY_DISRUPTION':
                    print(f"Processing supply-disruption event: {event.get('data', {})}")
                    # Impact on supply availability in the supply chain
                    self._handle_supply_disruption(event.get('data', {}))
                    
                elif event_type == 'SEASONAL_ADJUSTMENT':
                    print(f"Processing seasonal-adjustment event: {event.get('data', {})}")
                    # Adjusting market conditions to seasons
                    self._apply_seasonal_adjustments(event.get('data', {}))
                    
                elif event_type == 'LOGISTICS_DELAY':
                    print(f"Processing logistics-delay event: {event.get('data', {})}")
                    # Adjustment of logistics-related parameters
                    self._adjust_logistics(event.get('data', {}))
                    
                elif event_type == 'PRICE_FLUCTUATION':
                    print(f"Processing price-fluctuation event: {event.get('data', {})}")
                    # Update of price indices
                    self._update_price_index(event.get('data', {}))
                    
                # Record the outcome of the incident
                event_processing_results[event.get('id', str(len(event_processing_results)))] = {
                    'type': event_type,
                    'processed': True
                }
            
            # Deal with all incidents
            results = self.event_manager.process_events(events, current_state)
            
            # Record Events
            self.event_history.extend(events)
        
        # Process messages and consultations (using MessageManager)
        self._process_messages_and_negotiations()
    
        # Get and save updated status
        current_state = self.get_current_state()
        self.state_history.append(current_state)
        
        return {
            'state': current_state,
            'events_processed': event_processing_results,
            'total_events': len(events) if events else 0
        }
    
    def _update_market_demand(self, event_data):
        """
        Update market demand
                
        Args:
            event_data: Data containing information on changes in demand
                """
        # Achieving an updated market demand logic
        demand_factor = event_data.get('demand_factor', 1.0)
        product_id = event_data.get('product_id')
        
        # Update relevant enterprise demand projections
        if product_id:
            for enterprise in self.enterprises.values():
                if hasattr(enterprise, 'update_demand_forecast'):
                    enterprise.update_demand_forecast(product_id, demand_factor)
    
    def _handle_supply_disruption(self, event_data):
        """
        Treatment of supply interruptions
                
        Args:
            parameter: Data with interruption information
                """
        supplier_id = event_data.get('supplier_id')
        duration = event_data.get('duration', 1)
        
        # Notification of supply interruption enterprise
        for enterprise in self.enterprises.values():
            if hasattr(enterprise, 'notify_supply_disruption'):
                enterprise.notify_supply_disruption(supplier_id, duration)
    
    def _apply_seasonal_adjustments(self, event_data):
        """
        Application of seasonal adjustments
                
        Args:
            parameter: Data with seasonal information
                """
        season = event_data.get('season')
        raise NotImplementedError("Seasonal events must use the configured external environment policy")
    
    def _adjust_logistics(self, event_data):
        """
        Adjustment of logistics parameters
                
        Args:
            parameter: Data containing information on logistics adjustments
                """
        delay_factor = event_data.get('delay_factor', 1.0)
        raise NotImplementedError("Logistics events must use the configured external environment policy")
    
    def _update_price_index(self, event_data):
        """
        Update of price indices
                
        Args:
            parameter: Data containing information on price changes
                """
        price_factor = event_data.get('price_factor', 1.0)
        product_id = event_data.get('product_id')
        raise NotImplementedError("Price events must use the configured market policy")
    
    def _process_messages_and_negotiations(self):
        """
        Information processing and consultation process
                """
        # Use the message manager if it is set
        if hasattr(self, 'message_manager') and self.message_manager:
            # Call messageManager's program messages to process all pending messages
            self.message_manager.process_messages()
    
    def get_current_state(self):
        """
        Get Current Environmental Status
                
        Returns:
            Dect: State of the environment
                """
        # Secure access to network state to avoid empty references
        network_state = {}
        if hasattr(self, 'network_manager') and self.network_manager:
            try:
                network_state = self.network_manager.get_network_state()
            except Exception as e:
                print(f"Failed to retrieve network state: {str(e)}")
                network_state = {"status": "error", "message": str(e)}
        else:
            network_state = {"status": "not_available", "message": "enterprise Network Manager not set"}
        
        # Secure access to enterprise state
        enterprise_states = {}
        if hasattr(self, 'enterprises') and self.enterprises:
            enterprise_states = {eid: ent.get_state() for eid, ent in self.enterprises.items()}
        
        return {
            "time": self.time_manager.get_datetime(),
            "enterprises": enterprise_states,
            "network": network_state,
            "market_conditions": self._get_market_conditions()
        }
    
    def _get_market_conditions(self):
        """
        Access to current market conditions
                
        Returns:
            Dect: Market conditions
                """
        # This is where the logic of market conditions is created.
        return {
            "demand_level": 1.0,
            "price_index": 1.0,
            "supply_availability": 1.0
        }
    
    def add_enterprise(self, enterprise):
        """
        Add enterprise to Environment
                
        Args:
            Example: enterprise
                """
        # Use enterprise.id as enterprise Symbol
        self.enterprises[enterprise.id] = enterprise
        # Register enterprise to the message manager if a message manager is set
        if hasattr(self, 'message_manager') and self.message_manager:
            self.message_manager.register_enterprise(enterprise.id, enterprise)
    
    def remove_enterprise(self, enterprise_id):
        """
        Remove enterprise From Environment
                
        Args:
            enterprise_id: enterpriseID
                """
        if enterprise_id in self.enterprises:
            del self.enterprises[enterprise_id]
    
    def get_enterprise(self, enterprise_id):
        """
        Can not open message
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            Enterprise: enterprise Examples
                """
        return self.enterprises.get(enterprise_id)
    
    def close(self):
        """
        Close Environment
                """
        # The clean-up was handled by MessageManager
