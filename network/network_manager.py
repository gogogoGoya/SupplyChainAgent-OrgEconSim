"""
enterprise Network Manager Module

Management and maintenance of network enterprise
"""

import random
from typing import Dict, List, Set, Tuple


class NetworkManager:
    """
    enterprise Network Manager
        """
    def __init__(self):
        """
        Initialise Network Manager
                """
        self.enterprises = {}  # Information enterprise
        self.enterprise_instances = {}  # Example enterprise
        self.connections = {}  # enterprise Connection parameter:{connected_id: relationship_type}
        self.supply_chain_layers = []  # Supply chain level
        self.network_metrics = {}  # Network indicators
    
    def add_enterprise(self, enterprise_id: str, enterprise_info: Dict, enterprise_instance=None):
        """
        Add enterprise to Network
                
        Args:
            enterprise_id: enterpriseID
            enterprise_info: enterprise Info
            enterprise_instance: enterprise Examples (optional)
                """
        self.enterprises[enterprise_id] = enterprise_info
        self.connections[enterprise_id] = {}
        if enterprise_instance:
            self.enterprise_instances[enterprise_id] = enterprise_instance
    
    def add_enterprises(self, enterprises: Dict[str, Dict], enterprise_instances: Dict[str, object] = None):
        """
        Batch Add enterprise
                
        Args:
            Enterprises: enterprise Dictionary
            enterprise_instances: enterprise Examples Dictionary (optional)
                """
        for enterprise_id, enterprise_info in enterprises.items():
            # If enterprise case dictionary is provided, try to get the corresponding enterprise example
            instance = enterprise_instances.get(enterprise_id) if enterprise_instances else None
            self.add_enterprise(enterprise_id, enterprise_info, instance)
    
    def add_connection(self, from_id: str, to_id: str, relationship_type: str, metadata: Dict = None):
        """
        Add enterprise Connection
                
        Args:
            from_id: SourceenterpriseID
            to_id: TargetenterpriseID
            relationship_type: Relationship type (supplier, customer, company etc.)
            Metadata: Extra metadata
                """
        if from_id not in self.enterprises or to_id not in self.enterprises:
            raise ValueError(f"Enterprise ID {from_id} or {to_id} does not exist")
        
        connection_data = {
            "type": relationship_type,
            "metadata": metadata or {}
        }
        self.connections[from_id][to_id] = connection_data
    
    def remove_connection(self, from_id: str, to_id: str):
        """
        Remove enterprise Connection
                
        Args:
            from_id: SourceenterpriseID
            to_id: TargetenterpriseID
                """
        if from_id in self.connections and to_id in self.connections[from_id]:
            del self.connections[from_id][to_id]
    
    def get_connected_enterprises(self, enterprise_id: str, relationship_type: str = None) -> Dict[str, Dict]:
        """
        Access to enterprise connection enterprise
                
        Args:
            enterprise_id: enterpriseID
            relationship_type: Relationship type (optional)
                        
        Returns:
            dict: Connect enterpriseID to a map of relational data
                """
        if enterprise_id not in self.connections:
            return {}
        
        if relationship_type is None:
            return self.connections[enterprise_id]
        
        # Filter Specific Relationship Type
        filtered = {}
        for connected_id, connection_data in self.connections[enterprise_id].items():
            if connection_data["type"] == relationship_type:
                filtered[connected_id] = connection_data
        
        return filtered
    
    def build_supply_chain(self, layers: List[List[str]]):
        """
        Building supply chain networks
                
        Args:
            Players: Supply chain hierarchy, each of which contains a list of enterpriseIDs
                """
        self.supply_chain_layers = layers
        
        # Create supplier-client relationships between adjacent levels
        for i in range(len(layers) - 1):
            suppliers = layers[i]
            customers = layers[i + 1]
            
            for customer in customers:
                # Each client connects to multiple suppliers
                num_suppliers = random.randint(1, min(3, len(suppliers)))
                selected_suppliers = random.sample(suppliers, num_suppliers)
                
                for supplier in selected_suppliers:
                    self.add_connection(supplier, customer, "supplier", {"tier": i})
                    self.add_connection(customer, supplier, "customer", {"tier": i}) 
    
    def get_suppliers(self, enterprise_id: str) -> List[str]:
        """
        Vendor to obtain enterprise
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            list: supplier ID list
                """
        suppliers = []
        
        # Find all supplies connected to the current enterprise Business
        for potential_supplier, connection_data in self.get_connected_enterprises(enterprise_id).items():
            if connection_data["type"] == "supplier":
                suppliers.append(potential_supplier)
        
        # Find all enterprise current clients
        for other_id, connections in self.connections.items():
            if enterprise_id in connections and connections[enterprise_id]["type"] == "customer":
                if other_id not in suppliers:
                    suppliers.append(other_id)
        
        return suppliers
    
    def get_customers(self, enterprise_id: str) -> List[str]:
        """
        Client to access enterprise
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            list: client ID list
                """
        customers = []
        
        # Find all clients connected to the current enterprise
        for potential_customer, connection_data in self.get_connected_enterprises(enterprise_id).items():
            if connection_data["type"] == "customer":
                customers.append(potential_customer)
        
        # Find enterprise for all current suppliers
        for other_id, connections in self.connections.items():
            if enterprise_id in connections and connections[enterprise_id]["type"] == "supplier":
                if other_id not in customers:
                    customers.append(other_id)
        
        return customers
    
    def calculate_network_metrics(self):
        """
        Compute network indicators
                """
        # Calculate network density
        total_enterprises = len(self.enterprises)
        max_possible_connections = total_enterprises * (total_enterprises - 1)
        actual_connections = sum(len(cons) for cons in self.connections.values())
        
        density = actual_connections / max_possible_connections if max_possible_connections > 0 else 0
        
        # Calculate Average
        avg_degree = actual_connections / total_enterprises if total_enterprises > 0 else 0
        
        # Calculating a cluster factor
        clustering_coefficient = self._calculate_clustering_coefficient()
        
        # Central calculator indicators
        betweenness_centrality = self._calculate_betweenness_centrality()
        
        self.network_metrics = {
            "density": density,
            "average_degree": avg_degree,
            "clustering_coefficient": clustering_coefficient,
            "betweenness_centrality": betweenness_centrality,
            "total_enterprises": total_enterprises,
            "total_connections": actual_connections
        }
        
        return self.network_metrics
    
    def _calculate_clustering_coefficient(self) -> float:
        """
        Calculate a network cluster factor
                
        Returns:
            float: concentration factor
                """
        coefficients = []
        
        for enterprise_id in self.enterprises:
            # To the neighbors of enterprise
            neighbors = list(self.connections[enterprise_id].keys())
            k = len(neighbors)
            
            if k < 2:
                coefficients.append(0)
                continue
            
            # Calculate number of connections between neighbours
            actual_connections = 0
            for i in range(len(neighbors)):
                for j in range(i + 1, len(neighbors)):
                    if neighbors[j] in self.connections.get(neighbors[i], {}):
                        actual_connections += 1
            
            # Calculating a cluster factor
            max_possible_connections = k * (k - 1) / 2
            if max_possible_connections > 0:
                coefficients.append(actual_connections / max_possible_connections)
            else:
                coefficients.append(0)
        
        return sum(coefficients) / len(coefficients) if coefficients else 0
    
    def _calculate_betweenness_centrality(self) -> Dict[str, float]:
        """
        Central (simplified version) of nodes
                
        Returns:
            dict: enterpriseID to Media Central Map
                """
        centrality = {enterprise_id: 0.0 for enterprise_id in self.enterprises}
        all_pairs = [(s, t) for s in self.enterprises for t in self.enterprises if s != t]
        
        for s, t in all_pairs:
            # Find the shortest path (simplified) using BFS
            paths = self._find_shortest_paths(s, t)
            
            if not paths:
                continue
            
            # Calculate the proportion of the path through each node
            for node in centrality:
                if node == s or node == t:
                    continue
                
                paths_through_node = [path for path in paths if node in path]
                if paths_through_node:
                    centrality[node] += len(paths_through_node) / len(paths)
        
        # Standardization
        n = len(self.enterprises)
        if n > 2:
            for node in centrality:
                centrality[node] /= ((n - 1) * (n - 2))
        
        return centrality
    
    def _find_shortest_paths(self, start: str, end: str) -> List[List[str]]:
        """
        Find all shortest paths between two nodes
                
        Args:
            Start:
            End: Target Node
                        
        Returns:
            List: Shortest Path List
                """
        # Simplified BFS achieved
        if start == end:
            return [[start]]
        
        visited = {start}
        queue = [[start]]
        shortest_paths = []
        found = False
        
        while queue and not found:
            level_size = len(queue)
            
            for _ in range(level_size):
                path = queue.pop(0)
                last_node = path[-1]
                
                for neighbor in self.connections.get(last_node, {}):
                    if neighbor == end:
                        # Found a shortest path
                        shortest_paths.append(path + [neighbor])
                        found = True
                    elif neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(path + [neighbor])
        
        return shortest_paths
    
    def get_network_state(self) -> Dict:
        """
        Get Network Status
                
        Returns:
            Dect: Network Status
                """
        return {
            "enterprises": self.enterprises,
            "connections": self.connections,
            "supply_chain_layers": self.supply_chain_layers,
            "metrics": self.network_metrics
        }
    
    def clear(self):
        """
        Clear the network
                """
        self.enterprises = {}
        self.enterprise_instances = {}
        self.connections = {}
        self.supply_chain_layers = []
        self.network_metrics = {}
    
    def find_shortest_path(self, from_id: str, to_id: str) -> List[str]:
        """
        Find the shortest path between two enterprise
                
        Args:
            from_id: Start enterpriseID
            to_id: TargetenterpriseID
                        
        Returns:
            list: List of enterpriseIDs on the path
                """
        paths = self._find_shortest_paths(from_id, to_id)
        return paths[0] if paths else []
    
    def get_enterprise_by_type(self, enterprise_type: str) -> List[str]:
        """
        Get enterprise according to type
                
        Args:
            enterprise_type: enterprise Type
                        
        Returns:
            List: enterpriseID List
                """
        return [eid for eid, info in self.enterprises.items() if info.get("type") == enterprise_type]
    
    def get_enterprise_instance(self, enterprise_id: str) -> object:
        """
        Can not open message
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            Object: enterprise instance, return Noone if not available
                """
        return self.enterprise_instances.get(enterprise_id)
    
    def get_all_enterprise_instances(self) -> Dict[str, object]:
        """
        Get All Examples enterprise
                
        Returns:
            dict: map of enterpriseID to enterprise instance
                """
        return self.enterprise_instances.copy()

    def clear_connections(self):
        """
        Empty all relationships between enterprise
                
        The method will retain enterprise information, enterprise examples and supply chain levels, but will empty all links between enterprise
                """
        # Clear All Connections
        self.connections = {}
        
        # Reinitiate every enterprise connection dictionary
        for enterprise_id in self.enterprises.keys():
            self.connections[enterprise_id] = {}
        
        return {
            "success": True,
            "message": "All connections cleared",
            "enterprises_affected": len(self.enterprises)
        }
    
    def reset(self):
        """Reset derived metrics while keeping the configured firms and topology.

        A new independent simulation constructs a new NetworkManager.
        Environment initialization calls this method after building its topology.
        """
        self.network_metrics.clear()
