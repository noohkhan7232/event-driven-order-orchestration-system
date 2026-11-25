export interface paths {
    "/api/v1/admin/failed-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List All */
        get: operations["list_all_api_v1_admin_failed_jobs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/failed-jobs/{job_id}/discard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Discard Any */
        post: operations["discard_any_api_v1_admin_failed_jobs__job_id__discard_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/failed-jobs/{job_id}/replay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Replay Any */
        post: operations["replay_any_api_v1_admin_failed_jobs__job_id__replay_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/tenants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listing */
        get: operations["listing_api_v1_admin_tenants_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/tenants/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get */
        get: operations["get_api_v1_admin_tenants__tenant_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/tenants/{tenant_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate */
        post: operations["activate_api_v1_admin_tenants__tenant_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/tenants/{tenant_id}/suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend */
        post: operations["suspend_api_v1_admin_tenants__tenant_id__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/api-keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Keys */
        get: operations["list_keys_api_v1_api_keys_get"];
        put?: never;
        /** Issue */
        post: operations["issue_api_v1_api_keys_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/api-keys/{key_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke */
        delete: operations["revoke_api_v1_api_keys__key_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Login */
        post: operations["login_api_v1_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_api_v1_auth_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/signup": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Signup */
        post: operations["signup_api_v1_auth_signup_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/failed-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Owned */
        get: operations["list_owned_api_v1_failed_jobs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/failed-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detail */
        get: operations["detail_api_v1_failed_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/failed-jobs/{job_id}/discard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Discard Owned */
        post: operations["discard_owned_api_v1_failed_jobs__job_id__discard_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/failed-jobs/{job_id}/replay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Replay Owned */
        post: operations["replay_owned_api_v1_failed_jobs__job_id__replay_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/inventory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Inventory */
        get: operations["list_inventory_api_v1_inventory_get"];
        put?: never;
        /** Create Inventory */
        post: operations["create_inventory_api_v1_inventory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/inventory/low-stock": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Low Stock */
        get: operations["low_stock_api_v1_inventory_low_stock_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/inventory/warehouses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Warehouses */
        get: operations["list_warehouses_api_v1_inventory_warehouses_get"];
        put?: never;
        /** Create Warehouse */
        post: operations["create_warehouse_api_v1_inventory_warehouses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/inventory/{inventory_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Inventory */
        get: operations["get_inventory_api_v1_inventory__inventory_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Inventory */
        patch: operations["update_inventory_api_v1_inventory__inventory_id__patch"];
        trace?: never;
    };
    "/api/v1/inventory/{inventory_id}/adjustments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Adjust Inventory */
        post: operations["adjust_inventory_api_v1_inventory__inventory_id__adjustments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Orders */
        get: operations["list_orders_api_v1_orders_get"];
        put?: never;
        /** Create Order */
        post: operations["create_order_api_v1_orders_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream Orders */
        get: operations["stream_orders_api_v1_orders_stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders/{order_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Order */
        get: operations["get_order_api_v1_orders__order_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders/{order_id}/activity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Order Activity */
        get: operations["order_activity_api_v1_orders__order_id__activity_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders/{order_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Order */
        post: operations["cancel_order_api_v1_orders__order_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/orders/{order_id}/tracking/rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rotate Tracking */
        post: operations["rotate_tracking_api_v1_orders__order_id__tracking_rotate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/products": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listing */
        get: operations["listing_api_v1_products_get"];
        put?: never;
        /** Create */
        post: operations["create_api_v1_products_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/products/{product_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get */
        get: operations["get_api_v1_products__product_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Patch */
        patch: operations["patch_api_v1_products__product_id__patch"];
        trace?: never;
    };
    "/api/v1/public/track/{token}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Track */
        get: operations["track_api_v1_public_track__token__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/public/track/{token}/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream Tracking */
        get: operations["stream_tracking_api_v1_public_track__token__stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read */
        get: operations["read_api_v1_settings_notifications_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Patch */
        patch: operations["patch_api_v1_settings_notifications_patch"];
        trace?: never;
    };
    "/api/v1/settings/payments/razorpay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Config */
        get: operations["get_config_api_v1_settings_payments_razorpay_get"];
        /** Put Config */
        put: operations["put_config_api_v1_settings_payments_razorpay_put"];
        post?: never;
        /** Delete Config */
        delete: operations["delete_config_api_v1_settings_payments_razorpay_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/shipping": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Shipping */
        get: operations["read_shipping_api_v1_settings_shipping_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Shipping */
        patch: operations["update_shipping_api_v1_settings_shipping_patch"];
        trace?: never;
    };
    "/api/v1/shipments/{shipment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Shipment */
        patch: operations["update_shipment_api_v1_shipments__shipment_id__patch"];
        trace?: never;
    };
    "/api/v1/shipments/{shipment_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Shipment Event */
        post: operations["record_shipment_event_api_v1_shipments__shipment_id__events_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/system/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_api_v1_system_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/system/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Metrics */
        get: operations["metrics_api_v1_system_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/system/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Readiness */
        get: operations["readiness_api_v1_system_ready_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/system/workers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Workers */
        get: operations["workers_api_v1_system_workers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/tenants/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Own Tenant */
        get: operations["own_tenant_api_v1_tenants_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/tenants/register": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Register */
        post: operations["register_api_v1_tenants_register_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhook-endpoints": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listing */
        get: operations["listing_api_v1_webhook_endpoints_get"];
        put?: never;
        /** Create */
        post: operations["create_api_v1_webhook_endpoints_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhook-endpoints/{endpoint_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete */
        delete: operations["delete_api_v1_webhook_endpoints__endpoint_id__delete"];
        options?: never;
        head?: never;
        /** Patch */
        patch: operations["patch_api_v1_webhook_endpoints__endpoint_id__patch"];
        trace?: never;
    };
    "/api/v1/webhook-endpoints/{endpoint_id}/deliveries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Deliveries */
        get: operations["deliveries_api_v1_webhook_endpoints__endpoint_id__deliveries_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhook-endpoints/{endpoint_id}/deliveries/{delivery_id}/replay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Replay */
        post: operations["replay_api_v1_webhook_endpoints__endpoint_id__deliveries__delivery_id__replay_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhook-endpoints/{endpoint_id}/rotate-secret": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rotate */
        post: operations["rotate_api_v1_webhook_endpoints__endpoint_id__rotate_secret_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhook-endpoints/{endpoint_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ping */
        post: operations["ping_api_v1_webhook_endpoints__endpoint_id__test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/webhooks/payments/{provider}/{webhook_public_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Receive */
        post: operations["receive_api_v1_webhooks_payments__provider___webhook_public_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** ActivityEvent */
        ActivityEvent: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Id */
            id: string;
            /** Status */
            status: string;
        };
        /** ApiKeyCreate */
        ApiKeyCreate: {
            /** Expires At */
            expires_at?: string | null;
            /** Name */
            name: string;
            /** Scopes */
            scopes: ("orders:read" | "orders:write" | "catalog:read" | "catalog:write" | "inventory:read" | "inventory:write")[];
        };
        /** ApiKeyCreated */
        ApiKeyCreated: {
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /** Key Prefix */
            key_prefix: string;
            /** Last Used At */
            last_used_at: string | null;
            /** Name */
            name: string;
            /** Revoked At */
            revoked_at: string | null;
            /** Scopes */
            scopes: string[];
        };
        /** ApiKeyRead */
        ApiKeyRead: {
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key Prefix */
            key_prefix: string;
            /** Last Used At */
            last_used_at: string | null;
            /** Name */
            name: string;
            /** Revoked At */
            revoked_at: string | null;
            /** Scopes */
            scopes: string[];
        };
        /** CheckoutRead */
        CheckoutRead: {
            /** Amount Minor */
            amount_minor: number;
            /** Currency */
            currency: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Key Id */
            key_id: string;
            /** Provider */
            provider: string;
            /** Provider Order Id */
            provider_order_id: string;
        };
        /** DeliveryRead */
        DeliveryRead: {
            /** Attempts */
            attempts: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Delivered At */
            delivered_at: string | null;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /** Event Type */
            event_type: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Error */
            last_error: string | null;
            /** Last Status Code */
            last_status_code: number | null;
            /**
             * Next Attempt At
             * Format: date-time
             */
            next_attempt_at: string;
            /** Payload */
            payload: Record<string, never>;
            /** Replay Count */
            replay_count: number;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "delivered" | "failed" | "dead";
        };
        /** EndpointCreate */
        EndpointCreate: {
            /** Description */
            description?: string | null;
            /** Events */
            events: string[];
            /** Url */
            url: string;
        };
        /** EndpointPatch */
        EndpointPatch: {
            /** Description */
            description?: string | null;
            /** Events */
            events?: string[] | null;
            /** Is Active */
            is_active?: boolean | null;
            /** Url */
            url?: string | null;
        };
        /** EndpointRead */
        EndpointRead: {
            /** Consecutive Failures */
            consecutive_failures: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Description */
            description: string | null;
            /** Disabled At */
            disabled_at: string | null;
            /** Disabled Reason */
            disabled_reason: string | null;
            /** Events */
            events: string[];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Active */
            is_active: boolean;
            /**
             * Signing Secret Hint
             * @default whsec_********
             */
            signing_secret_hint: string;
            /** Url */
            url: string;
        };
        /** FailedJobRead */
        FailedJobRead: {
            /** Aggregate Id */
            aggregate_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Message */
            error_message: string;
            /** Event Id */
            event_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Queue Name */
            queue_name: string;
            /** Retry Count */
            retry_count: number;
            status: components["schemas"]["JobStatus"];
            /** Task Name */
            task_name: string;
            /** Tenant Id */
            tenant_id: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** InventoryAdjustment */
        InventoryAdjustment: {
            /** Delta */
            delta: number;
            /** Reason */
            reason: string;
        };
        /** InventoryCreate */
        InventoryCreate: {
            /** Available Quantity */
            available_quantity: number;
            /**
             * Low Stock Threshold
             * @default 10
             */
            low_stock_threshold: number;
            /** Product Name */
            product_name?: string | null;
            /** Sku */
            sku: string;
            /**
             * Warehouse Id
             * Format: uuid
             */
            warehouse_id: string;
        };
        /** InventoryRead */
        InventoryRead: {
            /** Available Quantity */
            available_quantity: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Low Stock Threshold */
            low_stock_threshold: number;
            /** Product Name */
            product_name: string;
            /** Reserved Quantity */
            reserved_quantity: number;
            /** Sku */
            sku: string;
            /**
             * Warehouse Id
             * Format: uuid
             */
            warehouse_id: string;
        };
        /** InventoryUpdate */
        InventoryUpdate: {
            /** Available Quantity */
            available_quantity?: number | null;
            /** Low Stock Threshold */
            low_stock_threshold?: number | null;
        };
        /**
         * JobStatus
         * @enum {string}
         */
        JobStatus: "queued" | "running" | "retrying" | "failed" | "completed" | "replayed" | "discarded";
        /** LoginRequest */
        LoginRequest: {
            /** Email */
            email: string;
            /** Password */
            password: string;
        };
        /** NotificationSettings */
        NotificationSettings: {
            /** Notifications Enabled */
            notifications_enabled: boolean;
        };
        /** OrderActivity */
        OrderActivity: {
            /** Payments */
            payments: components["schemas"]["PaymentActivity"][];
            /** Refunds */
            refunds: components["schemas"]["RefundActivity"][];
            /** Shipments */
            shipments: components["schemas"]["ShipmentActivity"][];
            /** Timeline */
            timeline: components["schemas"]["ActivityEvent"][];
        };
        /** OrderCreate */
        OrderCreate: {
            /** Buyer Email */
            buyer_email?: string | null;
            /** Buyer Name */
            buyer_name?: string | null;
            /** Buyer Phone */
            buyer_phone?: string | null;
            /** Currency */
            currency?: string | null;
            /** External Reference */
            external_reference?: string | null;
            /** Items */
            items: components["schemas"]["OrderItemCreate"][];
            /**
             * Payment Method
             * @default prepaid
             * @enum {string}
             */
            payment_method: "prepaid" | "cod";
        };
        /** OrderItemCreate */
        OrderItemCreate: {
            /** Quantity */
            quantity: number;
            /** Sku */
            sku: string;
        };
        /** OrderItemRead */
        OrderItemRead: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Product Name */
            product_name: string;
            /** Quantity */
            quantity: number;
            /** Sku */
            sku: string;
            /** Unit Price */
            unit_price: string;
            /** Warehouse Id */
            warehouse_id: string | null;
        };
        /** OrderRead */
        OrderRead: {
            /** Buyer Email */
            buyer_email: string | null;
            /** Buyer Name */
            buyer_name: string | null;
            /** Buyer Phone */
            buyer_phone: string | null;
            checkout: components["schemas"]["CheckoutRead"] | null;
            /** Currency */
            currency: string;
            /** External Reference */
            external_reference: string | null;
            /** Failure Reason */
            failure_reason: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Items */
            items: components["schemas"]["OrderItemRead"][];
            /** Payment Expires At */
            payment_expires_at: string | null;
            /**
             * Payment Method
             * @enum {string}
             */
            payment_method: "prepaid" | "cod";
            /** Payment Status */
            payment_status: string | null;
            /**
             * Payments
             * @default []
             */
            payments: components["schemas"]["PaymentRead"][];
            /**
             * Shipments
             * @default []
             */
            shipments: components["schemas"]["ShipmentRead"][];
            status: components["schemas"]["OrderStatus"];
            /** Total Amount */
            total_amount: string;
            /** Tracking Token */
            tracking_token: string;
            /** Tracking Url */
            tracking_url: string;
        };
        /**
         * OrderStatus
         * @enum {string}
         */
        OrderStatus: "created" | "validated" | "inventory_reserved" | "payment_pending" | "paid" | "fulfillment_requested" | "shipped" | "delivered" | "cancelled" | "failed";
        /** Page[FailedJobRead] */
        Page_FailedJobRead_: {
            /** Items */
            items: components["schemas"]["FailedJobRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** Page[InventoryRead] */
        Page_InventoryRead_: {
            /** Items */
            items: components["schemas"]["InventoryRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** Page[OrderRead] */
        Page_OrderRead_: {
            /** Items */
            items: components["schemas"]["OrderRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** Page[ProductRead] */
        Page_ProductRead_: {
            /** Items */
            items: components["schemas"]["ProductRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** Page[TenantRead] */
        Page_TenantRead_: {
            /** Items */
            items: components["schemas"]["TenantRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** Page[WarehouseRead] */
        Page_WarehouseRead_: {
            /** Items */
            items: components["schemas"]["WarehouseRead"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** PaymentActivity */
        PaymentActivity: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Id */
            id: string;
            /** Provider Status */
            provider_status: string | null;
            /** Status */
            status: string;
        };
        /** PaymentConfigRead */
        PaymentConfigRead: {
            /** Active */
            active: boolean;
            /** Key Id */
            key_id: string;
            /** Mode */
            mode: string;
            /** Webhook Public Id */
            webhook_public_id: string;
            /** Webhook Url */
            webhook_url: string;
        };
        /** PaymentConfigWrite */
        PaymentConfigWrite: {
            /** Key Id */
            key_id: string;
            /**
             * Key Secret
             * Format: password
             */
            key_secret: string;
            /**
             * Mode
             * @enum {string}
             */
            mode: "test" | "live";
            /**
             * Webhook Secret
             * Format: password
             */
            webhook_secret: string;
        };
        /** PaymentRead */
        PaymentRead: {
            /** Amount */
            amount: string;
            /** Failure Code */
            failure_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Provider */
            provider: string;
            /** Provider Transaction Id */
            provider_transaction_id: string | null;
            status: components["schemas"]["PaymentStatus"];
        };
        /**
         * PaymentStatus
         * @enum {string}
         */
        PaymentStatus: "initiated" | "authorized" | "captured" | "failed" | "refunded";
        /** ProductCreate */
        ProductCreate: {
            /** Currency */
            currency?: string | null;
            /** Description */
            description?: string | null;
            /** Name */
            name: string;
            /** Price */
            price: number | string;
            /** Sku */
            sku: string;
        };
        /** ProductRead */
        ProductRead: {
            /** Currency */
            currency: string;
            /** Description */
            description: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Name */
            name: string;
            /** Price */
            price: string;
            /** Sku */
            sku: string;
        };
        /** ProductUpdate */
        ProductUpdate: {
            /** Description */
            description?: string | null;
            /** Is Active */
            is_active?: boolean | null;
            /** Name */
            name?: string | null;
            /** Price */
            price?: number | string | null;
        };
        /** RefundActivity */
        RefundActivity: {
            /** Amount */
            amount: string;
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Currency */
            currency: string;
            /** Id */
            id: string;
            /** Status */
            status: string;
        };
        /** ReplayAccepted */
        ReplayAccepted: {
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @default replayed
             * @constant
             */
            status: "replayed";
        };
        /** ShipmentActivity */
        ShipmentActivity: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Id */
            id: string;
            /**
             * Shipment Id
             * Format: uuid
             */
            shipment_id: string;
            /** Status */
            status: string;
        };
        /** ShipmentEventCreate */
        ShipmentEventCreate: {
            /** Location */
            location?: string | null;
            /** Notes */
            notes?: string | null;
            status: components["schemas"]["ShipmentStatus"];
        };
        /** ShipmentRead */
        ShipmentRead: {
            /** Carrier */
            carrier: string;
            /** Estimated Delivery */
            estimated_delivery: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            status: components["schemas"]["ShipmentStatus"];
            /** Tracking Number */
            tracking_number: string | null;
            /** Tracking Url */
            tracking_url: string | null;
        };
        /**
         * ShipmentStatus
         * @enum {string}
         */
        ShipmentStatus: "pending" | "created" | "in_transit" | "delayed" | "delivered" | "cancelled";
        /** ShipmentUpdate */
        ShipmentUpdate: {
            /** Carrier */
            carrier?: string | null;
            /** Estimated Delivery */
            estimated_delivery?: string | null;
            /** Tracking Number */
            tracking_number?: string | null;
            /** Tracking Url */
            tracking_url?: string | null;
        };
        /** ShippingSettings */
        ShippingSettings: {
            /**
             * Shipping Provider
             * @enum {string}
             */
            shipping_provider: "mock" | "manual";
        };
        /** TenantRead */
        TenantRead: {
            /** Default Currency */
            default_currency: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Slug */
            slug: string;
            /** Status */
            status: string;
        };
        /** TenantRegister */
        TenantRegister: {
            /** Business Name */
            business_name: string;
            /** Invite Code */
            invite_code?: string | null;
            /**
             * Owner Email
             * Format: email
             */
            owner_email: string;
            /** Owner Name */
            owner_name: string;
            /** Owner Password */
            owner_password: string;
            /** Slug */
            slug?: string | null;
        };
        /** Token */
        Token: {
            /** Access Token */
            access_token: string;
            /**
             * Token Type
             * @default bearer
             */
            token_type: string;
        };
        /** UserCreate */
        UserCreate: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Full Name */
            full_name: string;
            /** Password */
            password: string;
            /** Tenant Slug */
            tenant_slug: string;
        };
        /** UserRead */
        UserRead: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Full Name */
            full_name: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Is Platform Admin */
            is_platform_admin: boolean;
            role: components["schemas"]["UserRole"];
            /** Tenant Id */
            tenant_id: string | null;
        };
        /**
         * UserRole
         * @enum {string}
         */
        UserRole: "customer" | "admin" | "vendor";
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WarehouseCreate */
        WarehouseCreate: {
            /** Code */
            code: string;
            /** Name */
            name: string;
            /** Region */
            region: string;
        };
        /** WarehouseRead */
        WarehouseRead: {
            /** Code */
            code: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Region */
            region: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_all_api_v1_admin_failed_jobs_get: {
        parameters: {
            query?: {
                tenant_id?: string | null;
                status?: components["schemas"]["JobStatus"] | null;
                task?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_FailedJobRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discard_any_api_v1_admin_failed_jobs__job_id__discard_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    replay_any_api_v1_admin_failed_jobs__job_id__replay_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReplayAccepted"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listing_api_v1_admin_tenants_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                status?: ("active" | "suspended") | null;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_TenantRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_api_v1_admin_tenants__tenant_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activate_api_v1_admin_tenants__tenant_id__activate_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    suspend_api_v1_admin_tenants__tenant_id__suspend_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_keys_api_v1_api_keys_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiKeyRead"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_api_v1_api_keys_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApiKeyCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiKeyCreated"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revoke_api_v1_api_keys__key_id__delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    login_api_v1_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Token"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_api_v1_auth_me_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    signup_api_v1_auth_signup_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_owned_api_v1_failed_jobs_get: {
        parameters: {
            query?: {
                status?: components["schemas"]["JobStatus"] | null;
                task?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_FailedJobRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detail_api_v1_failed_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FailedJobRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discard_owned_api_v1_failed_jobs__job_id__discard_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    replay_owned_api_v1_failed_jobs__job_id__replay_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReplayAccepted"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_inventory_api_v1_inventory_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_InventoryRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_inventory_api_v1_inventory_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InventoryCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InventoryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    low_stock_api_v1_inventory_low_stock_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InventoryRead"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_warehouses_api_v1_inventory_warehouses_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_WarehouseRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_warehouse_api_v1_inventory_warehouses_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WarehouseCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_inventory_api_v1_inventory__inventory_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                inventory_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InventoryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_inventory_api_v1_inventory__inventory_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                inventory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InventoryUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InventoryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    adjust_inventory_api_v1_inventory__inventory_id__adjustments_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                inventory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InventoryAdjustment"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InventoryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_orders_api_v1_orders_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                status?: components["schemas"]["OrderStatus"] | null;
                created_from?: string | null;
                created_to?: string | null;
                external_reference?: string | null;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_OrderRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_order_api_v1_orders_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OrderCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_orders_api_v1_orders_stream_get: {
        parameters: {
            query?: {
                order_id?: string | null;
                last_event_id?: number | null;
            };
            header?: {
                "last-event-id"?: number | null;
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_order_api_v1_orders__order_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    order_activity_api_v1_orders__order_id__activity_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderActivity"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_order_api_v1_orders__order_id__cancel_post: {
        parameters: {
            query?: {
                reason?: string | null;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rotate_tracking_api_v1_orders__order_id__tracking_rotate_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listing_api_v1_products_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                is_active?: boolean | null;
                q?: string | null;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ProductRead_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_api_v1_products_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProductCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_api_v1_products__product_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                product_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_api_v1_products__product_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                product_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProductUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    track_api_v1_public_track__token__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                token: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_tracking_api_v1_public_track__token__stream_get: {
        parameters: {
            query?: {
                last_event_id?: number | null;
            };
            header?: {
                "last-event-id"?: number | null;
            };
            path: {
                token: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_api_v1_settings_notifications_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationSettings"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_api_v1_settings_notifications_patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NotificationSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationSettings"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_config_api_v1_settings_payments_razorpay_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaymentConfigRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_config_api_v1_settings_payments_razorpay_put: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PaymentConfigWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaymentConfigRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_config_api_v1_settings_payments_razorpay_delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_shipping_api_v1_settings_shipping_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_shipping_api_v1_settings_shipping_patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ShippingSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_shipment_api_v1_shipments__shipment_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                shipment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ShipmentUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ShipmentRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_shipment_event_api_v1_shipments__shipment_id__events_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                shipment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ShipmentEventCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    health_api_v1_system_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    metrics_api_v1_system_metrics_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readiness_api_v1_system_ready_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    workers_api_v1_system_workers_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    own_tenant_api_v1_tenants_me_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    register_api_v1_tenants_register_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TenantRegister"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listing_api_v1_webhook_endpoints_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_api_v1_webhook_endpoints_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EndpointCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_api_v1_webhook_endpoints__endpoint_id__delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_api_v1_webhook_endpoints__endpoint_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EndpointPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EndpointRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    deliveries_api_v1_webhook_endpoints__endpoint_id__deliveries_get: {
        parameters: {
            query?: {
                status?: ("pending" | "delivered" | "failed" | "dead") | null;
                event_type?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    replay_api_v1_webhook_endpoints__endpoint_id__deliveries__delivery_id__replay_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
                delivery_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeliveryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rotate_api_v1_webhook_endpoints__endpoint_id__rotate_secret_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ping_api_v1_webhook_endpoints__endpoint_id__test_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "x-api-key"?: string | null;
            };
            path: {
                endpoint_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeliveryRead"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    receive_api_v1_webhooks_payments__provider___webhook_public_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider: string;
                webhook_public_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: boolean;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
