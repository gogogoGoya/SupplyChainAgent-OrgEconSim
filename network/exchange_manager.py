"""
multi-enterprise Unified module exchange

Coordination of transactions between different enterprises, including procurement, sales, inventory management, etc.
"""
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
from config.simulation_preset_config import (
    MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
    normalize_market_demand_mode,
)
from .trade_entities import (
    BuyRequest,
    SellRequest,
    OrderProposal,
    Order
)
import itertools

from contextvars import ContextVar

_id_counter = itertools.count(1)
_session_counter = ContextVar("exchange_session_counter", default=None)

def bind_exchange_counter(counter):
    return _session_counter.set(counter)

def reset_exchange_counter(token):
    _session_counter.reset(token)

def next_id(prefix: str) -> str:
    counter = _session_counter.get()
    if counter is None:
        return f"{prefix}_{next(_id_counter):06d}"
    value = counter[0]
    counter[0] += 1
    return f"{prefix}_{value:06d}"

def get_next_id_counter_value() -> int:
    global _id_counter
    counter = _session_counter.get()
    if counter is not None:
        return counter[0]
    value = next(_id_counter)
    _id_counter = itertools.count(value)
    return value

def restore_next_id_counter_value(next_value: int) -> None:
    global _id_counter
    counter = _session_counter.get()
    if counter is not None:
        counter[0] = max(1, int(next_value))
    else:
        _id_counter = itertools.count(max(1, int(next_value)))

class Exchange:
    """
    Category exchange
        
    Manage transactions between enterprise adjacent levels, recording sales, procurement requirements and orders
        """
    
    def __init__(self, exchange_id: str, upstream_layers: List[str], downstream_layers: List[str], trading_mode: str = "market"):
        """
        Initialise exchange
                
        Args:
            exchange_id: exchange Unique ID
            upstream_layers: List of names for the upper tier
            parameter: List of downstream level names
                """
        self.id = exchange_id
        self.upstream_layers = upstream_layers
        self.downstream_layers = downstream_layers
        self.trading_mode = trading_mode
        
        # Registration information enterprise
        self.upstream_enterprises: Dict[str, Dict] = {}  # Upstream enterprise {enterprise_id: enterprise_info}
        self.downstream_enterprises: Dict[str, Dict] = {}  # Downstream enterprise {enterprise_id: enterprise_info}
        # Order records
        self.orders: List[Order] = []
        
        # Transaction statistics
        self.statistics = {
            "total_orders": 0,
            "completed_orders": 0,
            "pending_orders": 0,
            "cancelled_orders": 0,
            "total_transaction_value": 0.0,
            "total_quantity": 0.0
        }

        # Add parameters after reconfiguration
        self.buy_requests: List[BuyRequest] = []
        self.sell_requests: List[SellRequest] = []
        self.proposals: List[OrderProposal] = []
        self.active_orders: List[Order] = []
        self.completed_orders: List[Order] = []
        self.market_stats: Dict = {}
        self.current_round_cursor: int = 0
        self.buy_request_ttl_grace_rounds = 2
        self.sell_request_ttl_grace_rounds = 3
        self.proposal_ttl_grace_rounds = 1
        self._implicit_supply_cursor: Dict[str, int] = {}

    def _set_round_cursor(self, round_id: int) -> None:
        self.current_round_cursor = max(self.current_round_cursor, int(round_id))

    def _resolve_buy_request_expiry(self, req: BuyRequest) -> int:
        due_floor = req.expected_due_round if req.expected_due_round is not None else req.created_round + 1
        return max(req.created_round, int(due_floor)) + self.buy_request_ttl_grace_rounds

    def _resolve_sell_request_expiry(self, req: SellRequest) -> int:
        return req.created_round + max(int(req.lead_time), 1) + self.sell_request_ttl_grace_rounds

    def _resolve_proposal_expiry(self, proposal: OrderProposal) -> int:
        return max(int(proposal.proposed_delivery_round), int(proposal.created_round) + 1) + self.proposal_ttl_grace_rounds

    def _normalize_delivery_round(
        self,
        candidate_round: Optional[int],
        current_round: int,
    ) -> tuple[int, Optional[int], bool]:
        floor_round = int(current_round) + 1
        if candidate_round is None:
            return floor_round, None, False
        original_round = int(candidate_round)
        normalized_round = max(floor_round, original_round)
        return normalized_round, original_round, normalized_round != original_round

    def _close_pending_proposals_for_request(
        self,
        *,
        buy_request_id: Optional[str] = None,
        sell_request_id: Optional[str] = None,
        round_id: int,
        status: str,
        release_buy_quantity: bool = False,
        release_sell_quantity: bool = False,
    ) -> None:
        for proposal in self.proposals:
            if proposal.status != "pending":
                continue
            if buy_request_id is not None and proposal.buy_request_id != buy_request_id:
                continue
            if sell_request_id is not None and proposal.sell_request_id != sell_request_id:
                continue
            if release_buy_quantity or release_sell_quantity:
                self._release_reserved_quantity(
                    proposal,
                    release_buy=release_buy_quantity,
                    release_sell=release_sell_quantity,
                )
            proposal.status = status
            proposal.closed_round = round_id
            proposal.dispatched = True

    def _close_buy_request(
        self,
        req: BuyRequest,
        *,
        round_id: int,
        status: str,
        superseded_by_request_id: Optional[str] = None,
    ) -> None:
        req.active = False
        req.lifecycle_status = status
        req.closed_round = round_id
        req.superseded_by_request_id = superseded_by_request_id
        self._close_pending_proposals_for_request(
            buy_request_id=req.request_id,
            round_id=round_id,
            status="expired" if status == "expired" else "superseded",
            release_sell_quantity=True,
        )

    def _close_sell_request(
        self,
        req: SellRequest,
        *,
        round_id: int,
        status: str,
        superseded_by_request_id: Optional[str] = None,
    ) -> None:
        req.active = False
        req.lifecycle_status = status
        req.closed_round = round_id
        req.superseded_by_request_id = superseded_by_request_id
        self._close_pending_proposals_for_request(
            sell_request_id=req.request_id,
            round_id=round_id,
            status="expired" if status == "expired" else "superseded",
            release_buy_quantity=True,
        )

    def _expire_stale_requests(self, round_id: int) -> None:
        for req in self.buy_requests:
            if req.active and req.expires_round is not None and round_id > req.expires_round:
                self._close_buy_request(req, round_id=round_id, status="expired")
        for req in self.sell_requests:
            if req.active and req.expires_round is not None and round_id > req.expires_round:
                self._close_sell_request(req, round_id=round_id, status="expired")

    def _expire_stale_proposals(self, round_id: int) -> None:
        for proposal in self.proposals:
            if proposal.status != "pending":
                continue
            if proposal.expires_round is None or round_id <= proposal.expires_round:
                continue
            proposal.status = "expired"
            proposal.closed_round = round_id
            proposal.dispatched = True
            self._release_reserved_quantity(proposal)

    def _expire_stale_entities(self, round_id: int) -> None:
        self._set_round_cursor(round_id)
        self._expire_stale_requests(round_id)
        self._expire_stale_proposals(round_id)

    @staticmethod
    def _registered_products(
        registry: Dict[str, Dict],
        company_id: str,
        field_name: str,
    ) -> Optional[set]:
        enterprise = registry.get(company_id) or {}
        info = enterprise.get("info") or {}
        if field_name not in info:
            return None
        return set(info.get(field_name) or [])

    def _is_registered_buyer_product(self, buyer_company_id: str, product_id: str) -> bool:
        purchasable = self._registered_products(
            self.downstream_enterprises,
            buyer_company_id,
            "purchasable_materials_idList",
        )
        return purchasable is None or product_id in purchasable

    def _is_registered_seller_product(self, seller_company_id: str, product_id: str) -> bool:
        salable = self._registered_products(
            self.upstream_enterprises,
            seller_company_id,
            "salable_products_idList",
        )
        return salable is None or product_id in salable

    def _validate_buy_request_product(self, buyer_company_id: str, product_id: str) -> None:
        if not self._is_registered_buyer_product(buyer_company_id, product_id):
            purchasable = self._registered_products(
                self.downstream_enterprises,
                buyer_company_id,
                "purchasable_materials_idList",
            )
            raise ValueError(
                f"Buyer {buyer_company_id} cannot buy product {product_id} on exchange {self.id}; "
                f"allowed purchasable products: {sorted(purchasable or [])}"
            )

    def _validate_sell_request_product(self, seller_company_id: str, product_id: str) -> None:
        if not self._is_registered_seller_product(seller_company_id, product_id):
            salable = self._registered_products(
                self.upstream_enterprises,
                seller_company_id,
                "salable_products_idList",
            )
            raise ValueError(
                f"Seller {seller_company_id} cannot sell product {product_id} on exchange {self.id}; "
                f"allowed salable products: {sorted(salable or [])}"
            )

    def _is_product_route_valid(
        self,
        seller_company_id: str,
        buyer_company_id: str,
        product_id: str,
    ) -> bool:
        return (
            self._is_registered_seller_product(seller_company_id, product_id)
            and self._is_registered_buyer_product(buyer_company_id, product_id)
        )

    def _get_valid_implicit_sellers(self, buyer_company_id: str, product_id: str) -> List[str]:
        return [
            seller_company_id
            for seller_company_id in self.upstream_enterprises.keys()
            if self._is_product_route_valid(seller_company_id, buyer_company_id, product_id)
        ]

    def _select_implicit_seller(self, buyer_company_id: str, product_id: str) -> Optional[str]:
        valid_sellers = self._get_valid_implicit_sellers(buyer_company_id, product_id)
        if not valid_sellers:
            return None
        cursor_key = f"{buyer_company_id}:{product_id}"
        cursor = self._implicit_supply_cursor.get(cursor_key, 0)
        self._implicit_supply_cursor[cursor_key] = cursor + 1
        return valid_sellers[cursor % len(valid_sellers)]


    def submit_buy_request(
        self,
        buyer_company_id: str,
        product_id: str,
        quantity: float,
        created_round: int,
        max_price: Optional[float] = None,
        expected_due_round: Optional[int] = None
    ) -> BuyRequest:
        self._expire_stale_entities(created_round)
        if buyer_company_id not in self.downstream_enterprises:
            raise ValueError(f"Unknown buyer company: {buyer_company_id}")
        self._validate_buy_request_product(buyer_company_id, product_id)
        req = BuyRequest(
            request_id=next_id("buy"),
            buyer_company_id=buyer_company_id,
            product_id=product_id,
            quantity=quantity,
            created_round=created_round,
            max_price=max_price,
            expected_due_round=expected_due_round
        )
        req.expires_round = self._resolve_buy_request_expiry(req)
        self.buy_requests.append(req)
        for existing_req in self.buy_requests:
            if existing_req.request_id == req.request_id:
                continue
            if (
                existing_req.lifecycle_status in ("open", "reserved")
                and existing_req.buyer_company_id == buyer_company_id
                and existing_req.product_id == product_id
            ):
                self._close_buy_request(
                    existing_req,
                    round_id=created_round,
                    status="superseded",
                    superseded_by_request_id=req.request_id,
                )
        return req

    def submit_sell_request(
        self,
        seller_company_id: str,
        product_id: str,
        quantity: float,
        created_round: int,
        min_price: Optional[float] = None,
        lead_time: int = 1,
    ) -> SellRequest:
        self._expire_stale_entities(created_round)
        if seller_company_id not in self.upstream_enterprises:
            raise ValueError(f"Unknown seller company: {seller_company_id}")
        self._validate_sell_request_product(seller_company_id, product_id)
        req = SellRequest(
            request_id=next_id("sell"),
            seller_company_id=seller_company_id,
            product_id=product_id,
            quantity=quantity,
            created_round=created_round,
            min_price=min_price,
            lead_time=lead_time,
        )
        req.expires_round = self._resolve_sell_request_expiry(req)
        self.sell_requests.append(req)
        for existing_req in self.sell_requests:
            if existing_req.request_id == req.request_id:
                continue
            if (
                existing_req.lifecycle_status in ("open", "reserved")
                and existing_req.seller_company_id == seller_company_id
                and existing_req.product_id == product_id
            ):
                self._close_sell_request(
                    existing_req,
                    round_id=created_round,
                    status="superseded",
                    superseded_by_request_id=req.request_id,
                )
        return req

    def generate_proposals(self, round_id: int) -> List[OrderProposal]:
        """
        Generate order presets
                
        Args:
            round_id: Current round
        Returns:
            List [OrderProposal]: List of transaction proposals
                """
        self._expire_stale_entities(round_id)
        new_proposals: List[OrderProposal] = []

        open_buys = sorted(
            [r for r in self.buy_requests if r.active and r.open_quantity > 0],
            key=lambda x: (x.created_round, x.request_id),
        )
        open_sells = sorted(
            [r for r in self.sell_requests if r.active and r.open_quantity > 0],
            key=lambda x: (x.created_round, x.request_id),
        )
        for buy_req in open_buys:
            if buy_req.open_quantity <= 0:
                continue

            for sell_req in open_sells:
                if buy_req.open_quantity <= 0:
                    break
                if sell_req.open_quantity <= 0:
                    continue
                if buy_req.product_id != sell_req.product_id:
                    continue
                if not self._is_product_route_valid(
                    sell_req.seller_company_id,
                    buy_req.buyer_company_id,
                    buy_req.product_id,
                ):
                    continue
                if not self._is_price_compatible(buy_req, sell_req):
                    continue

                matched_qty = min(buy_req.open_quantity, sell_req.open_quantity)
                if matched_qty <= 0:
                    continue
                proposed_delivery_round, original_delivery_round, delivery_round_rebased = self._normalize_delivery_round(
                    round_id + max(int(sell_req.lead_time), 1),
                    round_id,
                )
                proposal = OrderProposal(
                    proposal_id=next_id("proposal"),
                    buy_request_id=buy_req.request_id,
                    sell_request_id=sell_req.request_id,
                    buyer_company_id=buy_req.buyer_company_id,
                    seller_company_id=sell_req.seller_company_id,
                    product_id=buy_req.product_id,
                    quantity=matched_qty,
                    proposed_price=self._build_proposed_price(buy_req, sell_req),
                    proposed_delivery_round=proposed_delivery_round,
                    created_round=round_id,
                    route="explicit",
                    source_request_created_round=buy_req.created_round,
                    due_date_source="seller_lead_time",
                    original_proposed_delivery_round=original_delivery_round,
                    delivery_round_rebased=delivery_round_rebased,
                )
                proposal.expires_round = self._resolve_proposal_expiry(proposal)

                buy_req.open_quantity -= matched_qty
                sell_req.open_quantity -= matched_qty
                buy_req.active = buy_req.open_quantity > 0
                sell_req.active = sell_req.open_quantity > 0
                buy_req.last_proposal_round = round_id
                sell_req.last_proposal_round = round_id
                if buy_req.open_quantity <= 0 and buy_req.lifecycle_status == "open":
                    buy_req.lifecycle_status = "reserved"
                if sell_req.open_quantity <= 0 and sell_req.lifecycle_status == "open":
                    sell_req.lifecycle_status = "reserved"

                self.proposals.append(proposal)
                new_proposals.append(proposal)

        # Under the basic external demand model, the remaining unmatched downstream procurement needs, even if the current round is partially aligned
        # It should also continue to generate hidden scenarios, so as to avoid being stuck "with a proposal to stop " .
        if normalize_market_demand_mode(self.trading_mode) == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL:
            new_proposals.extend(self._generate_beer_game_implicit_supply_proposals(round_id))
        return new_proposals

    def _generate_beer_game_implicit_supply_proposals(self, round_id: int) -> List[OrderProposal]:
        """
        Beer game uses a fixed upstream/downstream link. Downstream procurement needs should be seen and decided by upstream sales department.
        Upstream sales are not required to be published first in the same round sell_request.
                """
        if not self.upstream_enterprises:
            return []

        proposals: List[OrderProposal] = []
        open_buys = sorted(
            [r for r in self.buy_requests if r.active and r.open_quantity > 0],
            key=lambda x: (x.created_round, x.request_id),
        )

        for buy_req in open_buys:
            if buy_req.open_quantity <= 0:
                continue
            upstream_company_id = self._select_implicit_seller(
                buy_req.buyer_company_id,
                buy_req.product_id,
            )
            if upstream_company_id is None:
                continue
            has_live_proposal = any(
                proposal.buy_request_id == buy_req.request_id and proposal.status in ("pending", "accepted", "confirmed")
                for proposal in self.proposals
            )
            if has_live_proposal:
                continue

            matched_qty = buy_req.open_quantity
            due_date_source = (
                "buyer_expected_due_round"
                if buy_req.expected_due_round is not None
                else "default_plus_one"
            )
            proposed_delivery_round, original_delivery_round, delivery_round_rebased = self._normalize_delivery_round(
                buy_req.expected_due_round if buy_req.expected_due_round is not None else round_id + 1,
                round_id,
            )
            proposal = OrderProposal(
                proposal_id=next_id("proposal"),
                buy_request_id=buy_req.request_id,
                sell_request_id=f"implicit_sell_{buy_req.request_id}",
                buyer_company_id=buy_req.buyer_company_id,
                seller_company_id=upstream_company_id,
                product_id=buy_req.product_id,
                quantity=matched_qty,
                proposed_price=buy_req.max_price if buy_req.max_price is not None else 0.0,
                proposed_delivery_round=proposed_delivery_round,
                created_round=round_id,
                route="implicit",
                source_request_created_round=buy_req.created_round,
                due_date_source=due_date_source,
                original_proposed_delivery_round=original_delivery_round,
                delivery_round_rebased=delivery_round_rebased,
            )
            proposal.expires_round = self._resolve_proposal_expiry(proposal)

            buy_req.open_quantity = 0
            buy_req.active = False
            buy_req.last_proposal_round = round_id
            if buy_req.lifecycle_status == "open":
                buy_req.lifecycle_status = "reserved"
            self.proposals.append(proposal)
            proposals.append(proposal)

        return proposals

    def dispatch_proposals(self) -> List[OrderProposal]:
        """
        Buyers and sellers will call for potential orders.
                """
        self._expire_stale_entities(self.current_round_cursor)
        proposal_list = []
        for proposal in self.proposals:
            if proposal.status != "pending" or proposal.dispatched:
                continue
            proposal_list.append(proposal)
        return proposal_list

    def get_proposal(self, proposal_id: str) -> Optional[OrderProposal]:
        """
        Return the exchange's authoritative proposal object by proposal ID.
                """
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    def handle_company_response(self, proposal_id: str, response: str, source: str) -> None:
        """
        Handle enterprise sound Response
                
        Args:
            parameter: Order Proposal ID
            response type, accept or reject
                """
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                if source == "buyer":
                    proposal.buyer_response = response
                else:
                    proposal.seller_response = response
                if proposal.seller_response != None and proposal.buyer_response != None:
                    proposal.dispatched = True

    def collect_proposal_responses(self) -> None:
        self._expire_stale_entities(self.current_round_cursor)
        for proposal in self.proposals:
            if proposal.status != "pending":
                continue
            if proposal.buyer_response == "accept" and proposal.seller_response == "accept":
                proposal.status = "accepted"
                proposal.closed_round = self.current_round_cursor
            elif proposal.buyer_response == "reject" or proposal.seller_response == "reject":
                proposal.status = "rejected"
                proposal.closed_round = self.current_round_cursor
                self._release_reserved_quantity(proposal)
            else:
                # As long as one side does not respond, keep the pending.
                # Allows step-by-step confirmation across round for buyer / seller.
                continue

    def confirm_orders(self) -> List[Order]:
        self._expire_stale_entities(self.current_round_cursor)
        new_orders: List[Order] = []
        for proposal in self.proposals:
            if proposal.status != "accepted":
                continue
            if not self._is_product_route_valid(
                proposal.seller_company_id,
                proposal.buyer_company_id,
                proposal.product_id,
            ):
                proposal.status = "rejected"
                proposal.closed_round = self.current_round_cursor
                self._release_reserved_quantity(proposal)
                continue
            proposal.status = "confirmed"
            proposal.closed_round = self.current_round_cursor
            planned_delivery_round, original_planned_delivery_round, order_delivery_rebased = self._normalize_delivery_round(
                proposal.proposed_delivery_round,
                self.current_round_cursor,
            )
            order = Order(
                order_id=next_id("order"),
                proposal_id=proposal.proposal_id,
                buyer_company_id=proposal.buyer_company_id,
                seller_company_id=proposal.seller_company_id,
                product_id=proposal.product_id,
                quantity=proposal.quantity,
                agreed_price=proposal.proposed_price,
                created_round=proposal.created_round,
                planned_delivery_round=planned_delivery_round,
                due_date_source=proposal.due_date_source,
                original_planned_delivery_round=proposal.original_proposed_delivery_round
                if proposal.original_proposed_delivery_round is not None
                else original_planned_delivery_round,
                delivery_round_rebased=proposal.delivery_round_rebased or order_delivery_rebased,
            )
            self.orders.append(order)
            new_orders.append(order)
            for buy_req in self.buy_requests:
                if buy_req.request_id == proposal.buy_request_id and buy_req.lifecycle_status in ("open", "reserved"):
                    buy_req.lifecycle_status = "fulfilled"
                    buy_req.closed_round = self.current_round_cursor
            for sell_req in self.sell_requests:
                if sell_req.request_id == proposal.sell_request_id and sell_req.lifecycle_status in ("open", "reserved"):
                    sell_req.lifecycle_status = "fulfilled"
                    sell_req.closed_round = self.current_round_cursor
        return new_orders

    def get_confirm_orders(self) -> List[Order]:
        """
        Backward compatibility interface: returns all confirmed orders without changing their status.
        The new logic should give priority to the buyer/seller orientation consumption interface to avoid the invisibility of one side to the back.
                """
        return [order for order in self.orders if order.status == "confirmed"]

    def get_confirm_orders_for_buyer(self, buyer_company_id: str) -> List[Order]:
        buyer_orders: List[Order] = []
        for order in self.orders:
            if (
                order.status == "confirmed"
                and order.buyer_company_id == buyer_company_id
                and not order.buyer_processed
            ):
                buyer_orders.append(order)
                order.buyer_processed = True
                if order.seller_processed:
                    order.status = "delivered"
        return buyer_orders

    def get_confirm_orders_for_seller(self, seller_company_id: str) -> List[Order]:
        seller_orders: List[Order] = []
        for order in self.orders:
            if (
                order.status == "confirmed"
                and order.seller_company_id == seller_company_id
                and not order.seller_processed
            ):
                seller_orders.append(order)
                order.seller_processed = True
                if order.buyer_processed:
                    order.status = "delivered"
        return seller_orders


    @staticmethod
    def _is_price_compatible(buy_req: BuyRequest, sell_req: SellRequest) -> bool:
        if buy_req.max_price is not None and sell_req.min_price is not None:
            return buy_req.max_price >= sell_req.min_price
        return True

    @staticmethod
    def _build_proposed_price(
        buy_req: BuyRequest,
        sell_req: SellRequest,
    ) -> Optional[float]:
        if buy_req.max_price is None and sell_req.min_price is None:
            return None
        if buy_req.max_price is None:
            return sell_req.min_price
        if sell_req.min_price is None:
            return buy_req.max_price
        return (buy_req.max_price + sell_req.min_price) / 2.0


    def _release_reserved_quantity(
        self,
        proposal: OrderProposal,
        *,
        release_buy: bool = True,
        release_sell: bool = True,
    ) -> None:
        if release_buy:
            for buy_req in self.buy_requests:
                if buy_req.request_id == proposal.buy_request_id:
                    if buy_req.lifecycle_status in ("expired", "superseded", "fulfilled"):
                        continue
                    buy_req.open_quantity = min(buy_req.quantity, buy_req.open_quantity + proposal.quantity)
                    buy_req.active = buy_req.open_quantity > 0
                    if buy_req.active:
                        buy_req.lifecycle_status = "open"
        if release_sell:
            for sell_req in self.sell_requests:
                if sell_req.request_id == proposal.sell_request_id:
                    if sell_req.lifecycle_status in ("expired", "superseded", "fulfilled"):
                        continue
                    sell_req.open_quantity = min(sell_req.quantity, sell_req.open_quantity + proposal.quantity)
                    sell_req.active = sell_req.open_quantity > 0
                    if sell_req.active:
                        sell_req.lifecycle_status = "open"

    # ----------------------------
    # ----------------------------
    
    def register_upstream_enterprise(self, enterprise_id: str, enterprise_name: str, enterprise_info: Dict = None) -> bool:
        """
        Upstream enterprise
                
        Args:
            enterprise_id: enterpriseID
            parameter: enterprise Name
            enterprise_info: enterprise Additional information
                        
        Returns:
            Bool: Successful registration
                """
        if enterprise_id in self.upstream_enterprises:
            print(f"Warning: enterprise {enterprise_name} is already registered upstream on exchange {self.id}")
            return False
        
        self.upstream_enterprises[enterprise_id] = {
            "enterprise_id": enterprise_id,
            "enterprise_name": enterprise_name,
            "registered_at": datetime.now(),
            "info": enterprise_info or {}
        }
        print(f"Registered upstream enterprise {enterprise_name} on exchange {self.id}")
        return True
    
    def register_downstream_enterprise(self, enterprise_id: str, enterprise_name: str, enterprise_info: Dict = None) -> bool:
        """
        Register downstream enterprise
                
        Args:
            enterprise_id: enterpriseID
            parameter: enterprise Name
            enterprise_info: enterprise Additional information
                        
        Returns:
            Bool: Successful registration
                """
        if enterprise_id in self.downstream_enterprises:
            print(f"Warning: enterprise {enterprise_name} is already registered downstream on exchange {self.id}")
            return False
        
        self.downstream_enterprises[enterprise_id] = {
            "enterprise_id": enterprise_id,
            "enterprise_name": enterprise_name,
            "registered_at": datetime.now(),
            "info": enterprise_info or {}
        }
        print(f"Registered downstream enterprise {enterprise_name} on exchange {self.id}")
        return True
          

class ExchangeManager:
    def __init__(self):
        self.exchanges = {}  # Exchanges keyed by exchange_id.
        self.layer_to_exchanges = {}  # Map to exchange {layer_name/enterprise_id: [exchange_ids]}
        self.trading_mode = "market"

    def set_trading_mode(self, trading_mode: str):
        if trading_mode:
            self.trading_mode = normalize_market_demand_mode(trading_mode)
            for exchange in self.exchanges.values():
                exchange.trading_mode = self.trading_mode

    def initialize_exchange(self, supply_chain_layers: list[list[str]]):
        """
        Initialization of exchange system
                
        Create instance exchange between adjacent levels based on level information
                
        Args:
            supply_chain_layers: Level information, e.g. [['Supplier','Manufacturer'], ['Distributor'], ['Retailer']
                """
        self.exchanges = {}
        self.layer_to_exchanges = {}
        
        # Create exchange between adjacent levels
        for i in range(len(supply_chain_layers) - 1):
            upstream_layers = supply_chain_layers[i]
            downstream_layers = supply_chain_layers[i + 1]
            
            # Create only exchange for each adjacent level
            exchange_id = f"{i}-{i+1}_exchange"
            # Use the first element of the hierarchy name as identification
            exchange = Exchange(exchange_id, upstream_layers, downstream_layers, trading_mode=self.trading_mode)
            self.exchanges[exchange_id] = exchange
            
            # Update map of enterprise name to exchange
            for upstream_layer in upstream_layers:
                if upstream_layer not in self.layer_to_exchanges:
                    self.layer_to_exchanges[upstream_layer] = []
                self.layer_to_exchanges[upstream_layer].append(exchange_id)
            
            for downstream_layer in downstream_layers:
                if downstream_layer not in self.layer_to_exchanges:
                    self.layer_to_exchanges[downstream_layer] = []
                self.layer_to_exchanges[downstream_layer].append(exchange_id)
        
        print(f"Exchange initialization complete: created {len(self.exchanges)} exchanges", self.exchanges)
        return self.exchanges
    
    def get_exchange(self, exchange_id: str) -> Optional[Exchange]:
        """
        Can not open message
                
        Args:
            exchange_id: exchangeID
                        
        Returns:
            Exchange: exchange instance, return Noone if none
                """
        return self.exchanges.get(exchange_id)
    
    def get_exchanges_by_enterprise(self, enterprise_id: str) -> List[Exchange]:
        """
        Get enterprise All registered exchange
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            List [Exchange]: exchange
                """
        exchange_ids = self.layer_to_exchanges.get(enterprise_id, [])
        return [self.exchanges[eid] for eid in exchange_ids if eid in self.exchanges]
    
    def get_all_exchanges(self) -> Dict[str, Exchange]:
        """
        Get All exchange
                
        Returns:
            Dict[str, Exchange]: All exchange Dictionaries
                """
        return self.exchanges
    
    def get_global_statistics(self) -> Dict:
        """
        Access global statistical information
                
        Returns:
            Dict: Global statistical information
                """
        total_stats = {
            "total_exchanges": len(self.exchanges),
            "total_orders": 0,
            "completed_orders": 0,
            "pending_orders": 0,
            "cancelled_orders": 0,
            "total_transaction_value": 0.0,
            "total_quantity": 0.0,
            "exchange_details": []
        }
        
        for exchange_id, exchange in self.exchanges.items():
            stats = exchange.get_statistics()
            total_stats["total_orders"] += stats["total_orders"]
            total_stats["completed_orders"] += stats["completed_orders"]
            total_stats["pending_orders"] += stats["pending_orders"]
            total_stats["cancelled_orders"] += stats["cancelled_orders"]
            total_stats["total_transaction_value"] += stats["total_transaction_value"]
            total_stats["total_quantity"] += stats["total_quantity"]
            
            total_stats["exchange_details"].append({
                "exchange_id": exchange_id,
                "upstream_layers": exchange.upstream_layers,
                "downstream_layers": exchange.downstream_layers,
                **stats
            })
        
        return total_stats

    def analyze_potential_transaction(self, round_id: int):
        """
        Analyse all orders
                
        Through exchange, analyse the order status and update statistical information
                """
        for exchange in self.exchanges.values():
            exchange.generate_proposals(round_id)

    def statistics_exchange_orders(self):
        """
        Count all orders
                
        Check all exchange for order status and update global information
                """
        for exchange in self.exchanges.values():
            exchange.collect_proposal_responses()
            exchange.confirm_orders()

    def get_detailed_exchange_info(self) -> Dict[str, Any]:
        """
        Get all exchange details
                
        Returns:
            Dict: Dictionary containing all exchange details, including enterprise information, order information, proposal information, etc.
                """
        detailed_info = {
            "global_info": {
                "total_exchanges": len(self.exchanges),
                "total_registered_enterprises": sum([
                    len(exchange.upstream_enterprises) + len(exchange.downstream_enterprises) 
                    for exchange in self.exchanges.values()
                ])
            },
            "exchanges": {}
        }
        
        for exchange_id, exchange in self.exchanges.items():
            # Information enterprise
            enterprises_info = {
                "upstream_enterprises": list(exchange.upstream_enterprises.keys()),
                "downstream_enterprises": list(exchange.downstream_enterprises.keys()),
                "upstream_count": len(exchange.upstream_enterprises),
                "downstream_count": len(exchange.downstream_enterprises)
            }
            
            # Buy Requests Information
            buy_requests_info = []
            total_buy_quantity = 0.0
            for req in exchange.buy_requests:
                req_dict = {
                    "request_id": req.request_id,
                    "buyer_company_id": req.buyer_company_id,
                    "product_id": req.product_id,
                    "quantity": req.quantity,
                    "open_quantity": req.open_quantity,
                    "created_round": req.created_round,
                    "max_price": req.max_price,
                    "expected_due_round": req.expected_due_round,
                    "active": req.active,
                    "lifecycle_status": req.lifecycle_status,
                    "expires_round": req.expires_round,
                    "closed_round": req.closed_round,
                    "superseded_by_request_id": req.superseded_by_request_id,
                    "last_proposal_round": req.last_proposal_round,
                }
                buy_requests_info.append(req_dict)
                total_buy_quantity += req.quantity
            
            # Sale Demand Information
            sell_requests_info = []
            total_sell_quantity = 0.0
            for req in exchange.sell_requests:
                req_dict = {
                    "request_id": req.request_id,
                    "seller_company_id": req.seller_company_id,
                    "product_id": req.product_id,
                    "quantity": req.quantity,
                    "open_quantity": req.open_quantity,
                    "created_round": req.created_round,
                    "min_price": req.min_price,
                    "lead_time": req.lead_time,
                    "active": req.active,
                    "lifecycle_status": req.lifecycle_status,
                    "expires_round": req.expires_round,
                    "closed_round": req.closed_round,
                    "superseded_by_request_id": req.superseded_by_request_id,
                    "last_proposal_round": req.last_proposal_round,
                }
                sell_requests_info.append(req_dict)
                total_sell_quantity += req.quantity
            
            # Cannot initialise Evolution's mail component.
            proposals_info = []
            total_proposal_quantity = 0.0
            for proposal in exchange.proposals:
                proposal_dict = {
                    "proposal_id": proposal.proposal_id,
                    "buy_request_id": proposal.buy_request_id,
                    "sell_request_id": proposal.sell_request_id,
                    "buyer_company_id": proposal.buyer_company_id,
                    "seller_company_id": proposal.seller_company_id,
                    "product_id": proposal.product_id,
                    "quantity": proposal.quantity,
                    "proposed_price": proposal.proposed_price,
                    "proposed_delivery_round": proposal.proposed_delivery_round,
                    "created_round": proposal.created_round,
                    "status": proposal.status,
                    "dispatched": proposal.dispatched,
                    "buyer_response": proposal.buyer_response,
                    "seller_response": proposal.seller_response,
                    "route": proposal.route,
                    "expires_round": proposal.expires_round,
                    "closed_round": proposal.closed_round,
                    "source_request_created_round": proposal.source_request_created_round,
                    "due_date_source": proposal.due_date_source,
                    "original_proposed_delivery_round": proposal.original_proposed_delivery_round,
                    "delivery_round_rebased": proposal.delivery_round_rebased,
                }
                proposals_info.append(proposal_dict)
                total_proposal_quantity += proposal.quantity
            
            # Organisation
            orders_info = []
            total_order_quantity = 0.0
            total_order_value = 0.0
            for order in exchange.orders:
                order_dict = {
                    "order_id": order.order_id,
                    "proposal_id": order.proposal_id,
                    "buyer_company_id": order.buyer_company_id,
                    "seller_company_id": order.seller_company_id,
                    "product_id": order.product_id,
                    "quantity": order.quantity,
                    "agreed_price": order.agreed_price,
                    "created_round": order.created_round,
                    "planned_delivery_round": order.planned_delivery_round,
                    "status": order.status,
                    "due_date_source": order.due_date_source,
                    "original_planned_delivery_round": order.original_planned_delivery_round,
                    "delivery_round_rebased": order.delivery_round_rebased,
                }
                orders_info.append(order_dict)
                total_order_quantity += order.quantity
                if order.agreed_price is not None:
                    total_order_value += order.quantity * order.agreed_price
            
            # Active and completed orders statistics
            active_orders_count = len([o for o in exchange.orders if o.status == "confirmed"])
            completed_orders_count = len([o for o in exchange.orders if o.status == "delivered"])
            
            # Quantitative information by product
            product_statistics = {}
            for req in exchange.buy_requests:
                if req.product_id not in product_statistics:
                    product_statistics[req.product_id] = {
                        "buy_quantity": 0.0,
                        "sell_quantity": 0.0,
                        "proposal_quantity": 0.0,
                        "order_quantity": 0.0
                    }
                product_statistics[req.product_id]["buy_quantity"] += req.quantity
            
            for req in exchange.sell_requests:
                if req.product_id not in product_statistics:
                    product_statistics[req.product_id] = {
                        "buy_quantity": 0.0,
                        "sell_quantity": 0.0,
                        "proposal_quantity": 0.0,
                        "order_quantity": 0.0
                    }
                product_statistics[req.product_id]["sell_quantity"] += req.quantity
            
            for proposal in exchange.proposals:
                if proposal.product_id not in product_statistics:
                    product_statistics[proposal.product_id] = {
                        "buy_quantity": 0.0,
                        "sell_quantity": 0.0,
                        "proposal_quantity": 0.0,
                        "order_quantity": 0.0
                    }
                product_statistics[proposal.product_id]["proposal_quantity"] += proposal.quantity
            
            for order in exchange.orders:
                if order.product_id not in product_statistics:
                    product_statistics[order.product_id] = {
                        "buy_quantity": 0.0,
                        "sell_quantity": 0.0,
                        "proposal_quantity": 0.0,
                        "order_quantity": 0.0
                    }
                product_statistics[order.product_id]["order_quantity"] += order.quantity
            
            # Organise exchange Details
            exchange_detail = {
                "id": exchange.id,
                "trading_mode": exchange.trading_mode,
                "upstream_layers": exchange.upstream_layers,
                "downstream_layers": exchange.downstream_layers,
                "enterprises": enterprises_info,
                "buy_requests": {
                    "list": buy_requests_info,
                    "count": len(buy_requests_info),
                    "total_quantity": total_buy_quantity,
                    "active_count": len([req for req in exchange.buy_requests if req.active]),
                    "active_open_quantity": sum(req.open_quantity for req in exchange.buy_requests if req.active),
                },
                "sell_requests": {
                    "list": sell_requests_info,
                    "count": len(sell_requests_info),
                    "total_quantity": total_sell_quantity,
                    "active_count": len([req for req in exchange.sell_requests if req.active]),
                    "active_open_quantity": sum(req.open_quantity for req in exchange.sell_requests if req.active),
                },
                "proposals": {
                    "list": proposals_info,
                    "count": len(proposals_info),
                    "total_quantity": total_proposal_quantity,
                    "pending_count": len([proposal for proposal in exchange.proposals if proposal.status == "pending"]),
                    "accepted_count": len([proposal for proposal in exchange.proposals if proposal.status == "accepted"]),
                    "rejected_count": len([proposal for proposal in exchange.proposals if proposal.status == "rejected"]),
                    "confirmed_count": len([proposal for proposal in exchange.proposals if proposal.status == "confirmed"]),
                    "expired_count": len([proposal for proposal in exchange.proposals if proposal.status == "expired"]),
                },
                "orders": {
                    "list": orders_info,
                    "count": len(orders_info),
                    "total_quantity": total_order_quantity,
                    "total_value": total_order_value,
                    "active_count": active_orders_count,
                    "completed_count": completed_orders_count
                },
                "product_statistics": product_statistics
            }
            
            detailed_info["exchanges"][exchange_id] = exchange_detail
        
        return detailed_info
