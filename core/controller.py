"""
controller module

As the control hub for the simulation system, it coordinates the components, manages enterprise decision-making,
Process messages and events, execute enterprise actions and control the overall simulation process.
"""

import os
import sys
import json
import time
import uuid
import logging
import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Dict, List, Any, Optional
from datetime import datetime
# Add the root directory to the Python path to ensure that all imports work properly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Use absolute import based on the root directory
from core.action_executor import ActionExecutor
from core.execution_boundary import execute_business_action, assert_execution_ready, EXECUTION_PROTOCOL
from enterprise.enterprise import Enterprise
from config.simulation_preset_config import (
    DEFAULT_BEER_GAME_CUSTOMER_DELIVERY_LEAD_TIME,
    DEFAULT_BEER_GAME_UNIT_PRICE,
    DEFAULT_COBWEB_CONFIG,
    DEFAULT_HERDING_CONFIG,
    DEFAULT_MARKET_DEMAND_MODE,
    DEFAULT_SERVICE_TOTAL_STEPS,
    DEFAULT_SHARED_RESOURCE_CONFIG,
    EXTERNAL_MARKET_ORDER_ENTERPRISE_ID,
    LEGACY_MARKET_DEMAND_MODE_BEER_GAME,
    MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
    normalize_market_demand_mode,
    get_active_enterprise_configs,
)

# Import Component Class
from .environment.environment import Environment
from .async_time_manager import AsyncTimeManager
from .event_manager import EventManager
from .market_manager import MarketManager
from network.exchange_manager import ExchangeManager
from network.network_manager import NetworkManager
from message.message_manager import MessageManager
class Controller:
    """
    Simulation System controller
        
    As the central control hub of the simulation system, coordinating the components,
    Managing enterprise decision-making, handling news and events, and carrying out enterprise actions,
    And control the whole simulation process.
        """
    def __init__(self, config=None, enterprise_configs=None, relationship_configs=None):
        """
        Initialization controller
                
        Initialization controller core components and state
                
        Args:
            config: Simulate configuration dictionary
            enterprise_configs: enterprise Configuration List, imported from outside, priority above configuration file
            relationship_configs: enterprise Relationship Configuration List, imported from outside, priority above configuration file
                """
        # Save Configuration
        self.config = config or {}
        self.runtime_injection_config = (
            self.config.get("runtime_injection_config")
            or self.config.get("runtime_injection")
            or {}
        )
        
        # System State
        self.is_running = False
        self.enterprises = {}
        self.is_initialized = False
        self.total_steps = self.config.get("time_config", {}).get("total_steps", DEFAULT_SERVICE_TOTAL_STEPS)
        self.enterprise_configs = enterprise_configs or get_active_enterprise_configs()
        self.enterprise_role_ids = self._build_enterprise_role_ids(self.enterprise_configs)
        
        # Component Reference
        self.environment = None
        self.time_manager = None
        self.event_manager = None
        self.network_manager = None
        self.message_manager = None
        self.market_manager = None
        self.exchange_manager = None
        
        # Automatically create and initialize components if configuration is provided
        if config:
            self.create_components()

            print("Creating enterprises")
            try:
                # Prioritize external incoming configuration, otherwise use the current master link unified preset
                enterprise_configs = self.enterprise_configs
                # Create enterprise instance to ensure use of the profile ID
                enterprises = {}
                for enterprise_config in enterprise_configs:
                    # Ensure that enterpriseID uses the value in the configuration
                    enterprise = Enterprise(config=enterprise_config)
                    # Directly setting ID to avoid Enterprise class overlay
                    enterprise.id = enterprise_config['id']
                    enterprises[enterprise.id] = enterprise
                    print(f"Created enterprise: {enterprise.name}; ID: {enterprise.id}; tier: {enterprise.tier}")
                
                # Initialization of business modules
                for enterprise in enterprises.values():
                    enterprise.initialize_default_modules()
                # Register enterprise to controller
                for enterprise in enterprises.values():
                    self.register_enterprise(enterprise)
                print(f"Enterprise creation complete; controller now manages {len(self.enterprises)} enterprises")
            except Exception as e:
                print(f"Failed to create enterprises: {str(e)}")
                import traceback
                traceback.print_exc()
            
            self.initialize_network_structure()
            # Initializing Environment
            if self.environment and hasattr(self.environment, 'reset'):
                self.environment.reset()
                # Initialization of network structure and environment enterprise
                self.initialize_environment()

    @staticmethod
    def _build_enterprise_role_ids(enterprise_configs: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
        """
        The enterprise tier extrapolates the id of the current upstream/downstream role in the main link.

        So that the controller no longer relies on `Supplier / Manufacturer / Distributor / Retailer` for writing to die.
        Literally, following the enterprise level of the configuration for automatic recognition.
                """
        ordered_enterprises = sorted(
            enterprise_configs,
            key=lambda item: (item.get("tier", 0), item.get("id", "")),
        )
        role_ids = {
            "supplier": None,
            "manufacturer": None,
            "distributor": None,
            "retailer": None,
        }
        if not ordered_enterprises:
            return role_ids
        role_ids["supplier"] = ordered_enterprises[0]["id"]
        role_ids["retailer"] = ordered_enterprises[-1]["id"]
        if len(ordered_enterprises) > 1:
            role_ids["manufacturer"] = ordered_enterprises[1]["id"]
        if len(ordered_enterprises) > 2:
            role_ids["distributor"] = ordered_enterprises[2]["id"]
        return role_ids
    
    def set_environment(self, environment):
        """
        Set Environment Examples
                
        Args:
            Other Organiser
                """
        self.environment = environment
        # Two-way Association
        if hasattr(environment, 'set_controller'):
            environment.set_controller(self)
      

    

    def set_message_manager(self, message_manager):
        """
        Set Message Manager Example
                
        Args:
            message_manager: instance of MessageManager
                """
        self.message_manager = message_manager
    
    def create_components(self):
        """
        Create all core components according to configuration
                """
        # Create Time Manager
        self.time_manager = AsyncTimeManager()
        
        # Create Action Executor
        self.action_executor = ActionExecutor()

        # Create Event Manager
        self.event_manager = EventManager()

        # Create an environmental market module
        environment_config = self.config.get("environment_config", {})
        self.market_manager = MarketManager(
            self.get_enterprise_instance,
            total_steps=self.config.get("time_config", {}).get("total_steps"),
            demand_mode=environment_config.get("market_demand_mode", DEFAULT_MARKET_DEMAND_MODE),
            beer_game_demand_series=environment_config.get("beer_game_demand_series"),
            beer_game_customer_delivery_lead_time=environment_config.get(
                "beer_game_customer_delivery_lead_time",
                DEFAULT_BEER_GAME_CUSTOMER_DELIVERY_LEAD_TIME,
            ),
            beer_game_unit_price=environment_config.get(
                "beer_game_unit_price",
                DEFAULT_BEER_GAME_UNIT_PRICE,
            ),
            beer_game_product_id=environment_config.get("beer_game_product_id", "beer"),
            cobweb_config=environment_config.get(
                "cobweb_config",
                DEFAULT_COBWEB_CONFIG,
            ),
            shared_resource_config=environment_config.get(
                "shared_resource_config",
                DEFAULT_SHARED_RESOURCE_CONFIG,
            ),
            herding_config=environment_config.get(
                "herding_config",
                DEFAULT_HERDING_CONFIG,
            ),
        )
        
        # Create Network Manager
        self.network_manager = NetworkManager()
        
        # Create exchange Manager
        self.exchange_manager = ExchangeManager()
        self.exchange_manager.set_trading_mode(
            environment_config.get("market_demand_mode", DEFAULT_MARKET_DEMAND_MODE)
        )

        # Create Message Manager
        self.message_manager = MessageManager()
        # Create an Environment
        self.environment = Environment()
        self.environment.set_controller(self)
        
        print("Controller core components initialized")
    
    def hanlde_network_action(self, step: int, action: dict):
        """
        Action to process network modules

        Args:
            step: Current time step
            Action: Dictionary containing action information
                """
        action_name = action.get("action_name")
        action_param = action.get("action_param", {})
        results = []
        if hasattr(self.network_manager, action_name):
            method = getattr(self.network_manager, action_name)
            try:
                result = method(**action_param)
                results.append({
                    "action": action_name,
                    "status": "success",
                    "result": result
                })

            except Exception as e:
                results.append({
                    "action": action_name,
                    "status": "error",
                    "error": str(e)
                })

    def _generate_initial_events(self):
        """
        Generate Initial Event
                """
        print("Controller is generating initial events...")
        
        if self.event_manager:
            # Generate initial sales orders for retailers
            if "retailer_1" in self.enterprises:
                retailer = self.enterprises["retailer_1"]
                # Create an initial sales order event
                self.event_manager.add_event({
                    "type": "new_sales_order",
                    "target": retailer.id,
                    "data": {
                        "order_id": "initial_order_1",
                        "customer_id": "customer_initial",
                        "products": {"finished_good_1": 5},
                        "priority": "normal"
                    },
                    "timestamp": 0
                })
    
    def on_decision_completed(self, enterprise_id, decisions):
        """
        enterprise Handling when decision-making is completed
                
        Args:
            enterprise_id: enterpriseID
            decisions: Enterprise decision results.
                """
        print(f"Enterprise {enterprise_id} completed its decision: {decisions}")
        
        # You can add here the logic of processing after the decision is made.
    
    def get_enterprise_instance(self, enterprise_id):
        """
        Can not open message
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            Example of Enterprise or None
                """
        if enterprise_id not in self.enterprises:
            return None
        return self.enterprises[enterprise_id]

    def register_enterprise(self, enterprise):
        """
        Register enterprise to control system
                
        Args:
            Example: enterprise
                        
        Returns:
            Bool: Successful registration
                """
        try:
            # Add to enterprise Dictionary
            self.enterprises[enterprise.id] = enterprise
            enterprise.controller = self
            enterprise.runtime_injection_config = self.runtime_injection_config
            
            # Setup enterprise Message Manager
            if self.message_manager and hasattr(enterprise, 'set_message_manager'):
                enterprise.set_message_manager(self.message_manager)
            
            if self.time_manager and hasattr(enterprise, 'set_time_manager'):
                enterprise.set_time_manager(self.time_manager)

            # Set up enterprise Environmental Market Manager
            if self.market_manager and hasattr(enterprise, 'set_market_manager'):
                enterprise.set_market_manager(self.market_manager)
            
            # Can not open message Device
            self._setup_enterprise_message_handlers(enterprise)
            
            # Add enterprise to the network manager
            if self.network_manager and hasattr(self.network_manager, 'add_enterprise'):
                self.network_manager.add_enterprise(enterprise.id, {
                    "name": enterprise.name,
                    "type": "integrated" if hasattr(enterprise, 'type') else "unknown"
                },enterprise)
            
            print(f"Enterprise {enterprise.name} ({enterprise.id}) registered with the controller")
            return True
        except Exception as e:
            print(f"Failed to register enterprise: {str(e)}")
            return False

    def set_runtime_injection_config(self, runtime_injection_config: Optional[Dict[str, Any]] = None):
        """Syncs the current running-level policy to the controller with registered enterprise."""
        self.runtime_injection_config = runtime_injection_config or {}
        self.config["runtime_injection_config"] = self.runtime_injection_config
        seed = self.runtime_injection_config.get("random_seed")
        if seed is not None and getattr(self, "_market_seed", None) != seed:
            self.market_manager.random.seed(seed)
            self._market_seed = seed
        for enterprise in getattr(self, "enterprises", {}).values():
            enterprise.runtime_injection_config = self.runtime_injection_config
    
    def _setup_enterprise_message_handlers(self, enterprise):
        """
        Setup message processing for enterprise Device
                
        Args:
            Example: enterprise
                """
        # Make sure enterprise has a message manager in place
        if not hasattr(enterprise, 'message_manager') or enterprise.message_manager is None:
            print(f"Warning: enterprise {enterprise.name} has no message manager")
            return
        
        # Use message manager register processor
        message_manager = enterprise.message_manager
        
        # Define an inventory update processing function
        def handle_inventory_update(message):
            print(f"Enterprise {enterprise.name} processing inventory update: {message.get('content', '')}")
            # You can add specific inventory update logic here.
            
        # Defines the price change process function
        def handle_price_change(message):
            print(f"Enterprise {enterprise.name} processing price update: {message.get('content', '')}")
            # Here you can add specific price logic.
            
        # Defines the order confirmation processing function
        def handle_order_confirmed(message):
            print(f"Enterprise {enterprise.name} processing order confirmation: {message.get('content', '')}")
            # Here you can add specific order confirmation logic.
            
        # Register message processor to message manager (use subscribe method)
        if hasattr(message_manager, 'subscribe'):
            message_manager.subscribe("inventory_update", handle_inventory_update)
            message_manager.subscribe("price_change", handle_price_change)
            message_manager.subscribe("order_confirmed", handle_order_confirmed)

    def restore_runtime_links_after_checkpoint(self):
        """Rebind runtime-only references removed from persisted checkpoints."""
        if not hasattr(self.market_manager, "random"):
            import random
            self.market_manager.random = random.Random()
        if self.environment is not None:
            self.environment.set_controller(self)
        if self.message_manager is not None:
            self.message_manager.subscribers = {}
            self.message_manager.enterprises = {}
        for enterprise in self.enterprises.values():
            enterprise.controller = self
            enterprise.runtime_injection_config = self.runtime_injection_config
            if self.message_manager is not None:
                enterprise.set_message_manager(self.message_manager)
                self.message_manager.enterprises[enterprise.id] = enterprise
            if self.time_manager is not None:
                enterprise.set_time_manager(self.time_manager)
            if self.market_manager is not None:
                enterprise.set_market_manager(self.market_manager)
            self._setup_enterprise_message_handlers(enterprise)
        if self.environment is not None:
            self.environment.enterprises = self.enterprises
    
    def _display_simulation_stats(self, step: int):
        """
        Show Simulation Statistical Information
                
        Args:
            step: Current time step
                """
        if (step + 1) % 10 != 0:
            return
            
        print(f"\n--- Time Step {step + 1} Statistics ---")
        print(f"Total enterprises: {len(self.enterprises)}")
        
        # You can add more statistical information
        # For example: number of orders, level of stock, sales, etc.
    
    def _save_simulation_results(self):
        """
        Save Simulation Results
                
        Returns:
            dict: Simulation results
                """
        try:
            # Collection of results data
            results = {
                "total_steps": self.time_manager.get_day(),
                "enterprise_count": len(self.enterprises),
                "timestamp": time.time()
            }
            
            # If the environment has a state history, it can be preserved.
            if hasattr(self.environment, 'state_history'):
                # Save only the last few states to reduce the amount of data
                results["last_states"] = self.environment.state_history[-10:] if len(self.environment.state_history) > 10 else self.environment.state_history
            
            print(f"Simulation results collected: {self.time_manager.get_day()} working days; {len(self.enterprises)} enterprises")
            return results
        except Exception as e:
            print(f"Failed to save simulation results: {str(e)}")
            return {"error": str(e)}
    
    def initialize_network_structure(self, structure: str = None, enterprises_config=None):
        """
        Initialize network structure
                
        Args:
            stringure: network structure type (linear, star, mesh, etc.), if not specified, use the configuration value
            enterprises_config: enterprise Configuration
                """
        print(f"Initializing network structure for {len(self.enterprises)} enterprises")
        
        # Fetch from configuration if no structure is specified
        if structure is None:
            structure = self.config.get("network_config", {}).get("structure", "linear")
        
        print(f"Network structure: {structure}")
        
        # Make sure network manager is initialized
        if not self.network_manager:
            print("Error: network manager is not initialized")
            return
        
        # Check if enterprise exists
        if len(self.enterprises) == 0:
            print("Warning: no enterprises detected")
            return
        
        # Build default supply chain level
        supply_chain_layers = []
        
        if structure == "linear":
            supply_chain_layers = [
                ["supplier_1"],  # First tier: suppliers
                ["manufacturer_1"],  # Second tier: manufacturer
                ["retailer_1"]  # Level 3: Retailers
            ]
        elif structure == "mesh":
            # For net structure, use all registered enterprise
            enterprise_ids = list(self.enterprises.keys())
            num_enterprises = len(enterprise_ids)
            
            if num_enterprises <= 2:
                # If enterprise is small, use a simple two-tiered structure
                mid_point = max(1, num_enterprises // 2)
                supply_chain_layers = [
                    enterprise_ids[:mid_point],  # First tier: suppliers
                    enterprise_ids[mid_point:]   # Second floor: clients
                ]
            else:
                # Split enterprise into three layers, ensuring at least one enterprise per layer
                third_point = max(1, num_enterprises // 3)
                two_thirds_point = max(third_point + 1, 2 * num_enterprises // 3)
                supply_chain_layers = [
                    enterprise_ids[:third_point],      
                    enterprise_ids[third_point:two_thirds_point],  
                    enterprise_ids[two_thirds_point:]  
                ]
        elif structure == "custom":
            # For a custom structure, according to the enterprise tier distribution level at registration
            for enterprise in self.enterprises.values():
                tier = enterprise.tier
                
                while len(supply_chain_layers) <= tier:
                    supply_chain_layers.append([])
                supply_chain_layers[tier].append(enterprise.id)
        if supply_chain_layers and self.network_manager and hasattr(self.network_manager, 'build_supply_chain'):
            self.network_manager.build_supply_chain(supply_chain_layers)
            print(f"Supply-chain network built with {len(supply_chain_layers)} tiers")
        # Set here exchangeexchange for each layer using network
        self.exchange_manager.initialize_exchange(supply_chain_layers)
        for enterprise in self.enterprises.values():
            exchanges = self.exchange_manager.get_exchanges_by_enterprise(enterprise.id)
            enterprise.set_exchange(exchanges)


    
    def initialize_environment(self):
        """
        Initializing Environment
                
        Register all enterprise into the environment
                """
        print(f"Initializing environment for {len(self.enterprises)} enterprises")
        
        if not self.environment:
            print("Error: environment is not initialized")
            return
        
        # Reset Environment
        self.environment.reset()
        
        # Register enterprise to Environment
        registered_count = 0
        for enterprise_id, enterprise in self.enterprises.items():
            try:
                self.environment.add_enterprise(enterprise)
                self.environment.enterprises[enterprise_id] = enterprise
                registered_count += 1
            except Exception as e:
                print(f"Failed to register enterprise {enterprise_id} with the environment: {str(e)}")
        # Ensure that the environment 's enterprises properties are set correctly
        if hasattr(self.environment, 'enterprises') and not isinstance(self.environment.enterprises, dict):
            self.environment.enterprises = self.enterprises
            print("Enterprise registry assigned directly to the environment")
        print(f"Environment initialization complete; registered {registered_count} enterprises")

    def stop(self):
        """
        Stop controller
                """
        self.is_running = False
        print("Controller stopped")

    # Simulation Executive
    # Common Step Implementation
    async def _execute_time_step(self, actions=None, log_file=None, part="standard_workflow"):
        """
        Simulation to execute a single time step 

        Args:
            step: Current time step
            Action lists for current steps
                """
        assert_execution_ready(self)
        if not actions or not isinstance(actions, list):
            return {
                "day": self.time_manager.get_day(),
                "environment_result": [],
                "enterprise_results": []
            }

        # Update Environment Time
        if hasattr(self.environment, 'current_time'):
            self.environment.current_time = self.time_manager.get_day()

        # # Environmental incident management - to handle the whole simulation
        # if self.environment:
        #     environment_result = self.environment.step(self.time_manager.get_day())

        # # Handle messages
        # if self.message_manager and hasattr(self.message_manager, 'process_messages'):
        #     self.message_manager.process_messages()

        # Implementation process
        actions_by_enterprise = {}
        actions_by_controller = {}
        for item in actions:
            eid = item.get('executor_id')
            if eid is not None:
                if eid == "controller":
                    actions_by_controller.setdefault(eid, []).append(item)
                else:
                    actions_by_enterprise.setdefault(eid, []).append(item)
        # Execute actions at the controller level
        for item in actions_by_controller.get("controller", []):
            action = item.get("action")
            dept = item.get("module_type")
            if dept == "network":
                self.hanlde_network_action(self.time_manager.get_day(), action)
        # Also execute actions at enterprise level
        grouped = defaultdict(lambda: defaultdict(list))
        for item in actions:
            eid = item.get("executor_id")
            dept = item.get("module_type")
            if eid != "controller":
                grouped[eid][dept].append(item)
         # Maximum queue length found
        max_depth = max((len(dept_actions) 
                 for e in grouped.values() 
                 for dept_actions in e.values()), default=0)
        actions_by_timeline = []
        for i in range(max_depth):
            batch = defaultdict(dict)
            for eid, dept_dict in grouped.items():
                for dept, queue in dept_dict.items():
                    if i < len(queue):
                        batch[eid][dept] = queue[i]  # Take i Behaviour
            if batch:
                actions_by_timeline.append(batch)
        flattened_results = []
        # department_time = defaultdict(int)
        # last_elapsed_time = 0 
        for action_in_timeline in actions_by_timeline:
            task_info = []
            results_in_line = []
            # Queue depth is preserved; ties commit in stable enterprise/department order.
            # The HTTP session gate prevents concurrent workflows and checkpoints.
            for enterprise_name, dept_dict in sorted(action_in_timeline.items()):
                for dept, action_info in sorted(dept_dict.items()):
                    action = action_info.get("action") or {}
                    result = await execute_business_action(
                        self, self.enterprises[enterprise_name], action, dept,
                        self.time_manager.get_tick(),
                    )
                    results_in_line.append(result)
                    task_info.append((enterprise_name, dept, action.get("action_name")))

            # Use task_info to handle anomalies and logs correctly
            normalized_results = []
            for i, result in enumerate(results_in_line):
                enterprise_name, dept, action_name = task_info[i]  # Get the right variable from parameter
                if isinstance(result, Exception):
                    print(f"Enterprise {enterprise_name}, department {dept}, failed during task execution: {str(result)}")
                    normalized_results.append({
                        "action_type": action_name,
                        "executor_id": enterprise_name,
                        "module_type": dept,
                        "status": "error",
                        "errors": [{
                            "code": "ACTION_EXECUTION_EXCEPTION",
                            "message": str(result),
                        }],
                    })
                    continue
                if result is None:
                    normalized_results.append({
                        "action_type": action_name,
                        "executor_id": enterprise_name,
                        "module_type": dept,
                        "status": "error",
                        "errors": [{
                            "code": "ACTION_RETURNED_NONE",
                            "message": (
                                f"Action '{action_name}' in module '{dept}' returned None"
                            ),
                        }],
                    })
                    continue
                if not hasattr(result, "to_dict") and not isinstance(result, dict):
                    normalized_results.append({
                        "action_type": action_name,
                        "executor_id": enterprise_name,
                        "module_type": dept,
                        "status": "error",
                        "errors": [{
                            "code": "INVALID_ACTION_RESULT",
                            "message": (
                                f"Action '{action_name}' in module '{dept}' returned "
                                f"unsupported result type {type(result).__name__}"
                            ),
                        }],
                    })
                    continue
                normalized_results.append(result)
                # if result.consumed_time is not None:
                #     department_time[result.module_id] += result.consumed_time
                if log_file and hasattr(result, "to_dict"):
                    with open(log_file, "a", encoding="utf-8") as f:
                        print(f"Enterprise {enterprise_name}, department {dept}, executed action: {result.action_type}\n{result.to_dict()}\n")
            
            flattened_results.extend(normalized_results)
            # total_elapsed = max(department_time.values(), default=0)
            # delta = total_elapsed - last_elapsed_time  
            # if delta > 0:
            #     await self.time_manager.run_timer(delta)
            # last_elapsed_time = total_elapsed
        if log_file and part == "standard_workflow":
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"Time step {self.time_manager.get_day()}: enterprise {enterprise_name} completed the current workflow\n")
        
        return {
            "day": self.time_manager.get_day(),
            "environment_result": [],
            "execution_protocol": EXECUTION_PROTOCOL,
            "enterprise_results": flattened_results
        }

    # Independently release the start, end and advance of the simulation to angent
    async def start_simulation(self, total_steps: int = None, environment=None):
        """
        Initialize the simulation and support repeated step advancement.
                """

        print("===== Initializing Simulation Environment =====")

        if environment:
            self.set_environment(environment)

        if total_steps is None:
            total_steps = self.config.get("time_config", {}).get("total_steps", 100)

        self.total_steps = total_steps      # Maximum number of total steps
        if self.market_manager:
            self.market_manager.set_total_steps(total_steps)
        self.is_running = True

        if not hasattr(self, "enterprises"):
            self.enterprises = {}

        print(f"Simulation initialization complete; maximum steps: {self.total_steps}")

    def configure_simulation(
        self,
        total_steps: int = None,
        market_demand_mode: str = None,
        beer_game_demand_series: list = None,
        beer_game_customer_delivery_lead_time: int = None,
        beer_game_unit_price: float = None,
        beer_game_product_id: str = None,
        cobweb_config: Dict[str, Any] = None,
        shared_resource_config: Dict[str, Any] = None,
        herding_config: Dict[str, Any] = None
    ):
        """
        Updates the operational level configuration. Used to synchronize the external Agent manager with the market demand model round before the formal advance.
                """
        if total_steps is not None:
            self.total_steps = int(total_steps)
            self.config.setdefault("time_config", {})["total_steps"] = self.total_steps
            if self.market_manager:
                self.market_manager.set_total_steps(self.total_steps)

        if self.market_manager:
            self.market_manager.configure(
                demand_mode=market_demand_mode,
                beer_game_demand_series=beer_game_demand_series,
                beer_game_customer_delivery_lead_time=beer_game_customer_delivery_lead_time,
                beer_game_unit_price=beer_game_unit_price,
                beer_game_product_id=beer_game_product_id,
                cobweb_config=cobweb_config,
                shared_resource_config=shared_resource_config,
                herding_config=herding_config,
            )
        if market_demand_mode and self.exchange_manager:
            self.exchange_manager.set_trading_mode(
                normalize_market_demand_mode(market_demand_mode)
            )

        return {
            "total_steps": self.total_steps,
            "final_round": max(0, self.total_steps - 1),
            "market": self.market_manager.get_config() if self.market_manager else {}
        }

    def get_simulation_context(self):
        market_config = self.market_manager.get_config() if self.market_manager else {}
        trade_mode = self.exchange_manager.trading_mode if self.exchange_manager else None
        normalized_market_mode = normalize_market_demand_mode(
            market_config.get("demand_mode")
        )
        normalized_trade_mode = normalize_market_demand_mode(trade_mode)
        is_scheduled_external_demand_mode = (
            normalized_market_mode == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
            and normalized_trade_mode == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
        )
        return {
            "total_steps": self.total_steps,
            "final_round": max(0, self.total_steps - 1),
            "market_demand_mode": normalized_market_mode,
            "trade_mode": normalized_trade_mode,
            "is_scheduled_external_demand_mode": is_scheduled_external_demand_mode,
            "is_beer_game_mode": (
                is_scheduled_external_demand_mode
                or (
                    market_config.get("demand_mode") == LEGACY_MARKET_DEMAND_MODE_BEER_GAME
                    and trade_mode == LEGACY_MARKET_DEMAND_MODE_BEER_GAME
                )
            ),
            "is_cobweb_mode": market_config.get("demand_mode") == "cobweb",
            "is_shared_resource_mode": market_config.get("demand_mode") == "shared_resource_market",
            "is_herding_mode": market_config.get("demand_mode") == "herding_market",
            "beer_game_demand_series": market_config.get("beer_game_demand_series"),
            "beer_game_customer_delivery_lead_time": market_config.get("beer_game_customer_delivery_lead_time"),
            "beer_game_unit_price": market_config.get("beer_game_unit_price"),
            "beer_game_product_id": market_config.get("beer_game_product_id"),
            "cobweb_config": market_config.get("cobweb_config"),
            "cobweb_history": market_config.get("cobweb_history"),
            "shared_resource_config": market_config.get("shared_resource_config"),
            "shared_resource_state": market_config.get("shared_resource_state"),
            "shared_resource_history": market_config.get("shared_resource_history"),
            "herding_config": market_config.get("herding_config"),
            "herding_state": market_config.get("herding_state"),
            "herding_history": market_config.get("herding_history"),
            "external_environment": deepcopy(
                getattr(self, "external_environment_state", {"enabled": False})
            ),
        }

    def get_bullwhip_metrics(self):
        def variance(values):
            if len(values) < 2:
                return 0.0
            mean = sum(values) / len(values)
            return sum((value - mean) ** 2 for value in values) / len(values)

        def series_from_pairs(pairs):
            by_round = defaultdict(float)
            for round_id, quantity in pairs:
                by_round[int(round_id)] += quantity or 0
            return [
                {"round": round_id, "quantity": quantity}
                for round_id, quantity in sorted(by_round.items())
            ]

        def dense_series_from_pairs(pairs, final_round: Optional[int]):
            sparse_series = series_from_pairs(pairs)
            sparse_map = {item["round"]: item["quantity"] for item in sparse_series}
            if final_round is None:
                if not sparse_map:
                    return []
                final_round = max(sparse_map.keys())
            return [
                {"round": round_id, "quantity": sparse_map.get(round_id, 0.0)}
                for round_id in range(max(0, int(final_round)) + 1)
            ]

        def ratio(variance_map, numerator_key, denominator_key):
            denominator = variance_map.get(denominator_key, 0)
            if denominator <= 0:
                return None
            return variance_map.get(numerator_key, 0) / denominator

        def build_flow_label(from_label: str, to_label: str) -> str:
            return f"{from_label} -> {to_label}"

        def build_dynamic_ratio_entries(variance_map, ordered_keys):
            entries = []
            for idx in range(1, len(ordered_keys)):
                numerator_key = ordered_keys[idx]
                denominator_key = ordered_keys[idx - 1]
                entries.append(
                    {
                        "numerator_key": numerator_key,
                        "denominator_key": denominator_key,
                        "ratio": ratio(variance_map, numerator_key, denominator_key),
                    }
                )
            return entries

        def is_effective_request(req) -> bool:
            return getattr(req, "lifecycle_status", None) not in {"superseded", "expired"}

        retailer_id = self.enterprise_role_ids.get("retailer") or EXTERNAL_MARKET_ORDER_ENTERPRISE_ID
        distributor_id = self.enterprise_role_ids.get("distributor")
        manufacturer_id = self.enterprise_role_ids.get("manufacturer")
        final_round = max(0, int(self.total_steps) - 1) if self.total_steps is not None else None
        ordered_enterprises = sorted(
            self.enterprise_configs,
            key=lambda item: (item.get("tier", 0), item.get("id", "")),
        )
        enterprise_by_id = {item.get("id"): item for item in ordered_enterprises}
        bottom_enterprise = ordered_enterprises[-1] if ordered_enterprises else None
        customer_dynamic_key = (
            f"customer_to_tier_{bottom_enterprise.get('tier')}"
            if bottom_enterprise else "customer_to_retailer"
        )

        canonical_edge_keys = [
            "retailer_to_distributor",
            "distributor_to_manufacturer",
            "manufacturer_to_supplier",
        ]
        edge_defs = []
        for idx, (upstream, downstream) in enumerate(reversed(list(zip(ordered_enterprises, ordered_enterprises[1:])))):
            dynamic_key = f"tier_{downstream.get('tier')}_to_tier_{upstream.get('tier')}"
            downstream_id = downstream.get("id")
            downstream_enterprise = self.enterprises.get(downstream_id)
            normalize_by_recipe = False
            recipe_raw_materials = {}
            if downstream_enterprise:
                production_modules = downstream_enterprise.business_modules.get("ProductionManager", [])
                if production_modules:
                    product_recipes = getattr(production_modules[0], "product_recipes", {}) or {}
                    first_recipe = next(iter(product_recipes.values()), None)
                    if isinstance(first_recipe, dict):
                        recipe_raw_materials = first_recipe.get("raw_materials", {}) or {}
                        normalize_by_recipe = bool(recipe_raw_materials)

            edge_defs.append(
                {
                    "canonical_key": canonical_edge_keys[idx] if idx < len(canonical_edge_keys) else None,
                    "dynamic_key": dynamic_key,
                    "upstream_enterprise_id": upstream.get("id"),
                    "upstream_enterprise_name": upstream.get("name", upstream.get("id")),
                    "upstream_tier": upstream.get("tier"),
                    "downstream_enterprise_id": downstream_id,
                    "downstream_enterprise_name": downstream.get("name", downstream.get("id")),
                    "downstream_tier": downstream.get("tier"),
                    "label": build_flow_label(
                        downstream.get("name", downstream.get("id")),
                        upstream.get("name", upstream.get("id")),
                    ),
                    "normalize_by_recipe": normalize_by_recipe,
                    "recipe_raw_materials": recipe_raw_materials,
                }
            )

        external_history = (
            self.market_manager.get_external_demand_history(retailer_id)
            if self.market_manager
            else []
        )
        customer_raw_series = series_from_pairs(
            (item.get("round", 0), item.get("quantity", 0))
            for item in external_history
        )
        customer_effective_series = dense_series_from_pairs(
            [
                (item.get("round", 0), item.get("quantity", 0))
                for item in external_history
            ],
            final_round,
        )

        raw_dynamic_flow_pairs = {
            customer_dynamic_key: [
                (item.get("round", 0), item.get("quantity", 0))
                for item in external_history
            ],
        }
        effective_dynamic_flow_pairs = {
            customer_dynamic_key: [
                (item.get("round", 0), item.get("quantity", 0))
                for item in external_history
            ],
        }
        raw_normalized_request_maps = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        effective_normalized_request_maps = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        edge_by_buyer_id = {}
        for edge in edge_defs:
            raw_dynamic_flow_pairs[edge["dynamic_key"]] = []
            effective_dynamic_flow_pairs[edge["dynamic_key"]] = []
            edge_by_buyer_id[edge["downstream_enterprise_id"]] = edge

        if self.exchange_manager:
            for exchange in self.exchange_manager.get_all_exchanges().values():
                for req in exchange.buy_requests:
                    edge = edge_by_buyer_id.get(req.buyer_company_id)
                    if edge is None:
                        continue
                    if edge.get("normalize_by_recipe"):
                        dynamic_key = edge["dynamic_key"]
                        raw_normalized_request_maps[dynamic_key][int(req.created_round)][req.product_id] += req.quantity or 0
                        if is_effective_request(req):
                            effective_normalized_request_maps[dynamic_key][int(req.created_round)][req.product_id] += req.quantity or 0
                        continue

                    raw_dynamic_flow_pairs[edge["dynamic_key"]].append((req.created_round, req.quantity))
                    if is_effective_request(req):
                        effective_dynamic_flow_pairs[edge["dynamic_key"]].append((req.created_round, req.quantity))

        def normalized_series_from_request_map(request_map, recipe_raw_materials):
            normalized_pairs = []
            for round_id, material_quantities in request_map.items():
                equivalents = []
                for material_id, quantity in material_quantities.items():
                    recipe_quantity = recipe_raw_materials.get(material_id)
                    if recipe_quantity:
                        equivalents.append(quantity / recipe_quantity)
                    else:
                        equivalents.append(quantity)
                normalized_pairs.append((round_id, min(equivalents) if equivalents else 0))
            return normalized_pairs

        for edge in edge_defs:
            if not edge.get("normalize_by_recipe"):
                continue
            dynamic_key = edge["dynamic_key"]
            recipe_raw_materials = edge.get("recipe_raw_materials") or {}
            raw_dynamic_flow_pairs[dynamic_key] = normalized_series_from_request_map(
                raw_normalized_request_maps[dynamic_key],
                recipe_raw_materials,
            )
            effective_dynamic_flow_pairs[dynamic_key] = normalized_series_from_request_map(
                effective_normalized_request_maps[dynamic_key],
                recipe_raw_materials,
            )

        dynamic_flow_sequence = [
            {
                "order": 0,
                "canonical_key": "customer_to_retailer",
                "dynamic_key": customer_dynamic_key,
                "label": build_flow_label("Customer", bottom_enterprise.get("name", retailer_id) if bottom_enterprise else retailer_id),
                "upstream_enterprise_id": bottom_enterprise.get("id") if bottom_enterprise else retailer_id,
                "upstream_enterprise_name": bottom_enterprise.get("name", retailer_id) if bottom_enterprise else retailer_id,
                "upstream_tier": bottom_enterprise.get("tier") if bottom_enterprise else None,
                "downstream_enterprise_id": "external_market",
                "downstream_enterprise_name": "Customer",
                "downstream_tier": None,
                "normalize_by_recipe": False,
            }
        ]
        dynamic_flow_sequence.extend(
            {
                "order": index + 1,
                **edge,
            }
            for index, edge in enumerate(edge_defs)
        )
        ordered_dynamic_keys = [item["dynamic_key"] for item in dynamic_flow_sequence]

        raw_dynamic_flows = {
            customer_dynamic_key: customer_raw_series,
        }
        effective_dynamic_flows = {
            customer_dynamic_key: customer_effective_series,
        }
        for edge in edge_defs:
            dynamic_key = edge["dynamic_key"]
            raw_dynamic_flows[dynamic_key] = series_from_pairs(raw_dynamic_flow_pairs[dynamic_key])
            effective_dynamic_flows[dynamic_key] = dense_series_from_pairs(
                effective_dynamic_flow_pairs[dynamic_key],
                final_round,
            )

        raw_tier_flows = {
            "customer_to_retailer": raw_dynamic_flows.get(customer_dynamic_key, []),
        }
        effective_tier_flows = {
            "customer_to_retailer": effective_dynamic_flows.get(customer_dynamic_key, []),
        }
        for edge in edge_defs:
            canonical_key = edge.get("canonical_key")
            if canonical_key:
                raw_tier_flows[canonical_key] = raw_dynamic_flows.get(edge["dynamic_key"], [])
                effective_tier_flows[canonical_key] = effective_dynamic_flows.get(edge["dynamic_key"], [])

        raw_variance_by_tier = {
            key: variance([item.get("quantity", 0) for item in series])
            for key, series in raw_tier_flows.items()
        }
        effective_variance_by_tier = {
            key: variance([item.get("quantity", 0) for item in series])
            for key, series in effective_tier_flows.items()
        }
        raw_dynamic_variance_by_tier = {
            key: variance([item.get("quantity", 0) for item in series])
            for key, series in raw_dynamic_flows.items()
        }
        effective_dynamic_variance_by_tier = {
            key: variance([item.get("quantity", 0) for item in series])
            for key, series in effective_dynamic_flows.items()
        }

        return {
            "series": effective_tier_flows,
            "variance": effective_variance_by_tier,
            "bullwhip_ratio": {
                "retailer_vs_customer": ratio(effective_variance_by_tier, "retailer_to_distributor", "customer_to_retailer"),
                "distributor_vs_retailer": ratio(effective_variance_by_tier, "distributor_to_manufacturer", "retailer_to_distributor"),
                "manufacturer_vs_distributor": ratio(effective_variance_by_tier, "manufacturer_to_supplier", "distributor_to_manufacturer")
            },
            "raw_series": raw_tier_flows,
            "raw_variance": raw_variance_by_tier,
            "raw_bullwhip_ratio": {
                "retailer_vs_customer": ratio(raw_variance_by_tier, "retailer_to_distributor", "customer_to_retailer"),
                "distributor_vs_retailer": ratio(raw_variance_by_tier, "distributor_to_manufacturer", "retailer_to_distributor"),
                "manufacturer_vs_distributor": ratio(raw_variance_by_tier, "manufacturer_to_supplier", "distributor_to_manufacturer")
            },
            "effective_series": effective_tier_flows,
            "effective_variance": effective_variance_by_tier,
            "effective_bullwhip_ratio": {
                "retailer_vs_customer": ratio(effective_variance_by_tier, "retailer_to_distributor", "customer_to_retailer"),
                "distributor_vs_retailer": ratio(effective_variance_by_tier, "distributor_to_manufacturer", "retailer_to_distributor"),
                "manufacturer_vs_distributor": ratio(effective_variance_by_tier, "manufacturer_to_supplier", "distributor_to_manufacturer")
            },
            "flow_sequence": dynamic_flow_sequence,
            "dynamic_series": effective_dynamic_flows,
            "dynamic_variance": effective_dynamic_variance_by_tier,
            "dynamic_bullwhip_ratio": build_dynamic_ratio_entries(
                effective_dynamic_variance_by_tier,
                ordered_dynamic_keys,
            ),
            "dynamic_raw_series": raw_dynamic_flows,
            "dynamic_raw_variance": raw_dynamic_variance_by_tier,
            "dynamic_raw_bullwhip_ratio": build_dynamic_ratio_entries(
                raw_dynamic_variance_by_tier,
                ordered_dynamic_keys,
            ),
            "dynamic_effective_series": effective_dynamic_flows,
            "dynamic_effective_variance": effective_dynamic_variance_by_tier,
            "dynamic_effective_bullwhip_ratio": build_dynamic_ratio_entries(
                effective_dynamic_variance_by_tier,
                ordered_dynamic_keys,
            ),
            "recording_notes": {
                "effective_filters": {
                    "buy_request_lifecycle_excluded": ["superseded", "expired"],
                    "round_axis_dense_zero_fill": True,
                },
                "manufacturer_to_supplier_normalization": "min_complete_package_equivalent_by_round",
                "dynamic_flow_keys_enabled": True,
            },
        }

    async def advance_simulation(self, workflow=None, log_file=None, part="standard_workflow"):
        """
        Push once, workflow, multiple step
        It can be used over and over again to advance time. Axis
                """

        if not self.is_running:
            print("Simulation is not running; call start_simulation first")
            return {
                "enterprise_results": [],
                "environment_result": None
            }
        
        # {\cHFFFFFF}{\cH00FFFF} print ("=== = advance simulation======")
        if log_file and part == "daily_system_actions":
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*70}\n")
                f.write(f"Working day {self.time_manager.get_day()} - {part}\n")
                f.write(f"{'='*70}\n")
                f.write(f"Execution time: {datetime.now().isoformat()}\n\n")
        # Parsing WorkFlow
        step_actions_map = {}
        if isinstance(workflow, dict) and "workflows" in workflow:
            step_actions_map = {
                int(step_key): actions
                for step_key, actions in workflow["workflows"].items()
            }
        elif isinstance(workflow, list):
            step_actions_map[0] = workflow
        has_executable_actions = any(
            isinstance(item, dict)
            and isinstance(item.get("action"), dict)
            and item["action"].get("action_name") != "action_pass"
            for actions_flow in step_actions_map.values()
            for item in (actions_flow or [])
        )
        has_pass_reason = any(
            isinstance(item, dict) and "pass_reason" in item
            for actions_flow in step_actions_map.values()
            for item in (actions_flow or [])
        )
        if has_pass_reason and not has_executable_actions:
            return {
                "enterprise_results": [],
                "environment_result": None
            }
        # Get the largest step in workflow
        max_step = max(step_actions_map.keys()) if step_actions_map else 0
        has_errors = False
        # Time step forward
        action_result = []

        for offset_step in range(max_step + 1):
            actions_flow = step_actions_map.get(offset_step, None)
            actions = [
                a for a in actions_flow
                if isinstance(a.get("action"), dict)
                and "action_name" in a["action"]
                and "action_param" in a["action"]
                and a["action"]["action_name"] != "action_pass"
            ]
            if actions:
                # Add department behavioural check before implementation
                action_list_by_dept = {}
                error_list = []
                has_error = False
                for item in actions:
                    executor = item["executor_id"]
                    module = item["module_type"]

                    if executor not in action_list_by_dept:
                        action_list_by_dept[executor] = {}
                    if module not in action_list_by_dept[executor]:
                        action_list_by_dept[executor][module] = []
                    action_list_by_dept[executor][module].append(item["action"])
                
                for executor_id, modules in action_list_by_dept.items():
                    for module_type, actions_by_dept in modules.items():
                        if executor_id not in self.enterprises:
                            error_list.append({
                                "action_type": "batch_execute",
                                "executor_id": executor_id,
                                "module_type": module_type,
                                "status": "failed",
                                "errors": [{
                                    "code": "UNKNOWN_ENTERPRISE",
                                    "message": (
                                        f"Enterprise '{executor_id}' is not registered "
                                        f"in current simulation session"
                                    ),
                                }],
                            })
                            has_error = True
                            break
                        enterprise = self.enterprises[executor_id]
                        managers = enterprise.business_modules.get(module_type)
                        if not managers:
                            error_list.append({
                                "action_type": "batch_execute",
                                "executor_id": executor_id,
                                "module_type": module_type,
                                "status": "failed",
                                "errors": [{
                                    "code": "UNKNOWN_MODULE_MANAGER",
                                    "message": (
                                        f"Enterprise '{executor_id}' has no registered "
                                        f"module manager for '{module_type}'"
                                    ),
                                }],
                            })
                            has_error = True
                            break
                        manager = managers[0]
                        check_response = manager.batch_execute(actions_by_dept)
                        check_response_dict = check_response.to_dict()
                        if check_response_dict["status"] != "success":
                            error_list.append(check_response_dict)
                            has_error = True
                            break
                if has_error:
                    action_result.extend(error_list)
                    has_errors = True
                    break

                result = await self._execute_time_step(actions, log_file, part)
                if not result or "enterprise_results" not in result:
                    continue
                action_result.extend(result["enterprise_results"])
                # self._display_simulation_stats(step)
        grouped = defaultdict(list)
        if not isinstance(action_result, list):
            return {}
        if has_errors:
            grouped["error"].append(action_result)
        else:
            for item in action_result:
                if hasattr(item, "to_dict"):
                    status = item.to_dict()["status"]
                    grouped[status].append(item)
                elif isinstance(item, dict):
                    status = item.get("status") or "error"
                    grouped[status].append(item)
                else:
                    print("item",item)
        return dict(grouped)


    async def end_simulation(self):
        """
        End simulation, save and return final results
                """ 
        print("===== Simulation Complete =====")
        self.is_running = False

        if self.environment:
            results = self._save_simulation_results()
        else:
            results = {}

        return results
    # One-time mock call, reserved for workflow testing
    async def run_simulation(self, total_steps: int = None, environment=None, workflow=None):
        """
        Run Simulation
                
        Args:
            total_steps: Total time steps, if not specified, using the configuration value
            Environmental examples (optional)
                
        Returns:
            dict: Simulation results
                """
        print("===== Starting Simulation =====")
        
        # Ensure environment is set
        if environment:
            print("Using the supplied environment instance")
            self.set_environment(environment)
        
        # If no total number of steps is specified, get from the configuration
        if total_steps is None:
            total_steps = self.config.get("time_config", {}).get("total_steps", DEFAULT_SERVICE_TOTAL_STEPS)
        
        # Make sure the controller has enterpries properties
        if not hasattr(self, 'enterprises'):
            self.enterprises = {}
        if workflow:
            workflows = workflow.get('workflows', {})
            if workflows:
                # Convert key to integer and find maximum value
                max_key = max(int(key) for key in workflows.keys())
                # The total number of steps is the maximum plus 1 (because the step starts at 0)
                total_steps = max_key + 1
            else:
                total_steps = 0
        print(f"Controller started simulation for {total_steps} steps")
        
        # Set the status of operation
        self.is_running = True
        
        # Simulation Results
        results = {}

        # Parsing workflow, build steps to an actions map
        step_actions_map = {}
        if workflow:
            workflows = workflow.get('workflows', {})
            for step_key, actions in workflows.items():
                step_actions_map[int(step_key)] = actions
        # Start the time axis cycle
        for step in range(total_steps):
            # Fetches actions for the current step
            actions = step_actions_map.get(step, None)
            if actions:
            # Timescale for implementation
                await self._execute_time_step(step, actions)
                
                # Show statistical information
                self._display_simulation_stats(step)
            
            # Check to stop
            if not self.is_running:
                print("Simulation stopped")
                break
        
        # Save Results
        if self.environment:
            results = self._save_simulation_results()
        
        return results

    def handle_enterprises_ordercheck(self):
        """
        Validation of orders
                """
        supplier_id = self.enterprise_role_ids.get("supplier")
        retailer_id = self.enterprise_role_ids.get("retailer")
        # Distribution of potential orders exchange
        self.exchange_manager.analyze_potential_transaction(self.time_manager.get_day())
        # Each enterprise sales/procurement department automatic receipt of potential orders
        for enterprise in self.enterprises.values():
            if enterprise.id != supplier_id:
                # Processing upward procurement
                ProcurementManagers = enterprise.business_modules.get("ProcurementManager")
                if ProcurementManagers:
                    ProcurementManager = ProcurementManagers[0]
                    ProcurementManager.receive_proposals()
            if enterprise.id != retailer_id:
                # Processing down sales orders
                SaleManagers = enterprise.business_modules.get("SalesManager")
                if SaleManagers:
                    SaleManager = SaleManagers[0]
                    SaleManager.receive_proposals()

    def handle_enterprises_orderstatistics(self):
        """
        Statistics on orders
                """
        supplier_id = self.enterprise_role_ids.get("supplier")
        retailer_id = self.enterprise_role_ids.get("retailer")
        self.exchange_manager.statistics_exchange_orders()
        # enterprise Sales/procurement department Establishment of orders
        for enterprise in self.enterprises.values():
            if enterprise.id != supplier_id:
                # Processing upward procurement
                ProcurementManagers = enterprise.business_modules.get("ProcurementManager")
                if ProcurementManagers:
                    ProcurementManager = ProcurementManagers[0]
                    ProcurementManager.build_orders_from_exchange()
            if enterprise.id != retailer_id:
                # Processing down sales orders
                SalesManagers = enterprise.business_modules.get("SalesManager")
                if SalesManagers:
                    SalesManager = SalesManagers[0]
                    SalesManager.build_orders_from_exchange()
            
