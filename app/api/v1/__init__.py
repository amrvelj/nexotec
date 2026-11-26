"""Router composition root: assembles every context's API router under
/v1. Not itself part of any bounded context, so it's the one place allowed
to import each context's api package directly rather than through its
public.py — it needs the APIRouter objects, not domain functions.
"""

from fastapi import APIRouter

from app.aftersales.api.service_orders import router as aftersales_service_orders_router
from app.api.v1.contact_vehicles import router as contact_vehicles_router
from app.api.v1.dealer_groups import router as dealer_groups_router
from app.api.v1.labour_rate_agreements import router as labour_rate_agreements_router
from app.api.v1.vehicle_parties import allocation_router as vehicle_party_allocation_router
from app.api.v1.vehicle_parties import holders_router as vehicle_party_holders_router
from app.contact.api.contacts import router as contacts_router
from app.integration.api.ai_prompts import router as integration_ai_prompts_router
from app.integration.api.call_payloads import router as integration_call_payloads_router
from app.integration.api.captions import router as integration_captions_router
from app.integration.api.connections import router as integration_connections_router
from app.integration.api.google_business_profile import router as integration_business_profile_router
from app.integration.api.mail import router as integration_mail_router
from app.integration.api.providers import router as integration_providers_router
from app.inventory.api.group import router as inventory_group_router
from app.inventory.api.ledger import router as inventory_ledger_router
from app.inventory.api.pricing import router as inventory_pricing_router
from app.inventory.api.publishing import router as inventory_publishing_router
from app.inventory.api.reservations import router as inventory_reservations_router
from app.inventory.api.stock_items import router as stock_items_router
from app.planning.api.bookings import router as planning_bookings_router
from app.planning.api.resources import router as planning_resources_router
from app.platform.administration.api.area_settings import router as area_settings_router
from app.platform.administration.api.configuration_copy import router as configuration_copy_router
from app.platform.administration.api.data_exports import router as data_exports_router
from app.platform.administration.api.labour_rates import router as labour_rates_router
from app.platform.administration.api.publishing_profile import router as publishing_profile_router
from app.platform.administration.api.setup import router as setup_router
from app.platform.administration.api.status_page import router as status_page_router
from app.platform.documents.api.branding import router as branding_router
from app.platform.documents.api.document_links import router as document_links_router
from app.platform.documents.api.document_template import router as document_template_router
from app.platform.documents.api.standard_text_seeds import router as standard_text_seeds_router
from app.platform.documents.api.standard_texts import router as standard_texts_router
from app.platform.health import router as health_router
from app.platform.identity.api.auth import account_router as auth_account_router
from app.platform.identity.api.auth import router as auth_router
from app.platform.identity.api.dealership_identity import router as dealership_identity_router
from app.platform.identity.api.dealership_memberships import router as dealership_memberships_router
from app.platform.identity.api.dealership_users import router as dealership_users_router
from app.platform.identity.api.me import router as me_router
from app.platform.identity.api.notifications import router as notifications_router
from app.platform.identity.api.onboarding import router as onboarding_router
from app.platform.identity.api.platform_staff import router as platform_staff_router
from app.platform.identity.api.recovery import router as platform_recovery_router
from app.platform.identity.api.support_sessions import router as support_sessions_router
from app.platform.identity.api.user_preferences import router as user_preferences_router
from app.platform.organisation.api.contact_groups import router as contact_groups_router
from app.platform.organisation.api.dealerships import router as dealerships_router
from app.platform.organisation.api.locations import router as locations_router
from app.platform.organisation.api.number_ranges import router as number_ranges_router
from app.platform.reference.api.reference_data import router as reference_data_router
from app.platform.reference.api.ui_translations import router as ui_translations_router
from app.reporting.api.public_stock import router as public_stock_router
from app.sales.api.contracts import router as sales_contracts_router
from app.sales.api.deals import router as sales_deals_router
from app.sales.api.documents import router as sales_documents_router
from app.sales.api.offers import router as sales_offers_router
from app.valuation.api.valuations import router as valuations_router
from app.vehicle.api.catalogue import router as vehicle_catalogue_router
from app.vehicle.api.catalogue_admin import router as vehicle_catalogue_admin_router
from app.vehicle.api.configuration import router as vehicle_configuration_router
from app.vehicle.api.identification import router as vehicle_identification_router
from app.vehicle.api.lookup import router as vehicle_lookup_router
from app.vehicle.api.vehicle_mdm import router as vehicle_mdm_router
from app.vehicle.api.vehicle_mdm_detail import router as vehicle_mdm_detail_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(dealerships_router)
api_v1_router.include_router(data_exports_router)
api_v1_router.include_router(dealership_users_router)
api_v1_router.include_router(dealership_memberships_router)
api_v1_router.include_router(dealership_identity_router)
api_v1_router.include_router(onboarding_router)
api_v1_router.include_router(setup_router)
api_v1_router.include_router(status_page_router)
api_v1_router.include_router(locations_router)
api_v1_router.include_router(branding_router)
api_v1_router.include_router(publishing_profile_router)
api_v1_router.include_router(public_stock_router)
api_v1_router.include_router(document_template_router)
api_v1_router.include_router(document_links_router)
api_v1_router.include_router(configuration_copy_router)
# KAN-805 PR-2: the settings store in Dealer Administration.
api_v1_router.include_router(area_settings_router)
api_v1_router.include_router(standard_texts_router)
api_v1_router.include_router(number_ranges_router)
api_v1_router.include_router(labour_rates_router)
api_v1_router.include_router(labour_rate_agreements_router)
# KAN-808: the planner's resources (S7), Dealer Administration behind the aftersales gate.
api_v1_router.include_router(planning_resources_router)
api_v1_router.include_router(planning_bookings_router)
api_v1_router.include_router(contact_groups_router)
api_v1_router.include_router(dealer_groups_router)
api_v1_router.include_router(platform_recovery_router)
api_v1_router.include_router(support_sessions_router)
api_v1_router.include_router(platform_staff_router)
api_v1_router.include_router(reference_data_router)
api_v1_router.include_router(ui_translations_router)
api_v1_router.include_router(standard_text_seeds_router)
api_v1_router.include_router(vehicle_lookup_router)
api_v1_router.include_router(vehicle_catalogue_router)
api_v1_router.include_router(vehicle_configuration_router)
api_v1_router.include_router(vehicle_identification_router)
api_v1_router.include_router(vehicle_catalogue_admin_router)
api_v1_router.include_router(vehicle_mdm_router)
api_v1_router.include_router(vehicle_party_allocation_router)
api_v1_router.include_router(vehicle_mdm_detail_router)
api_v1_router.include_router(vehicle_party_holders_router)
api_v1_router.include_router(contacts_router)
api_v1_router.include_router(contact_vehicles_router)
api_v1_router.include_router(stock_items_router)
api_v1_router.include_router(inventory_reservations_router)
api_v1_router.include_router(inventory_ledger_router)
api_v1_router.include_router(inventory_group_router)
api_v1_router.include_router(inventory_publishing_router)
api_v1_router.include_router(inventory_pricing_router)
api_v1_router.include_router(sales_deals_router)
api_v1_router.include_router(sales_offers_router)
api_v1_router.include_router(sales_contracts_router)
api_v1_router.include_router(sales_documents_router)
api_v1_router.include_router(valuations_router)
# KAN-810: the workshop order, behind the `aftersales` module switch.
api_v1_router.include_router(aftersales_service_orders_router)
api_v1_router.include_router(integration_providers_router)
api_v1_router.include_router(integration_connections_router)
api_v1_router.include_router(integration_ai_prompts_router)
api_v1_router.include_router(integration_mail_router)
api_v1_router.include_router(integration_business_profile_router)
api_v1_router.include_router(integration_call_payloads_router)
api_v1_router.include_router(integration_captions_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(user_preferences_router)
api_v1_router.include_router(me_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(auth_account_router)
