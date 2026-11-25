export const strings = {
  ops: {
    verificationPython: String.raw`import hashlib, hmac, re, time

def verify_oos_webhook(raw_body, headers, secret, now=None):
    # Call before parsing JSON. After success, parse JSON and deduplicate its signed id durably.
    try:
        headers = {key.lower(): value.strip() for key, value in headers.items()}
        pieces = headers["x-oos-signature"].split(",")
        if len(pieces) != 2:
            return False
        fields = dict(piece.strip().split("=", 1) for piece in pieces)
        if set(fields) != {"t", "v1"}:
            return False
        stamp, digest = fields["t"], fields["v1"]
        if headers.get("x-oos-timestamp") != stamp:
            return False
        if not re.fullmatch(r"[0-9]{1,12}", stamp) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return False
        if abs((time.time() if now is None else now) - int(stamp)) > 300:
            return False
        expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, digest.lower())
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
`,
    verificationNode: String.raw`const { createHmac, timingSafeEqual } = require("node:crypto");

function verifyOosWebhook(rawBody, headers, secret, now = Math.floor(Date.now() / 1000)) {
  // Call before parsing JSON. After success, parse JSON and deduplicate its signed id durably.
  if (!Buffer.isBuffer(rawBody)) return false;
  const h = new Headers(headers);
  const pieces = (h.get("X-OOS-Signature") || "").split(",");
  if (pieces.length !== 2) return false;
  const fields = new Map(pieces.map(piece => {
    const at = piece.indexOf("=");
    return [piece.slice(0, at).trim(), piece.slice(at + 1).trim()];
  }));
  if (fields.size !== 2 || !fields.has("t") || !fields.has("v1")) return false;
  const stamp = fields.get("t"), signature = fields.get("v1");
  if (h.get("X-OOS-Timestamp") !== stamp) return false;
  if (!/^[0-9]{1,12}$/.test(stamp) || !/^[0-9a-fA-F]{64}$/.test(signature)) return false;
  if (Math.abs(now - Number(stamp)) > 300) return false;
  const expected = createHmac("sha256", secret).update(Buffer.concat([Buffer.from(stamp + "."), rawBody])).digest();
  const received = Buffer.from(signature, "hex");
  return received.length === expected.length && timingSafeEqual(expected, received);
}
`,
    ownerOnly: "Store owner access required",
    title: "Settings and operations",
    name: "Name",
    save: "Save changes",
    done: "Done",
    confirm: "Confirm",
    cancel: "Back",
    copy: "Copy",
    copied: "Copied.",
    copyFailed: "Could not copy. Select the value and copy it manually.",
    empty: "Nothing to show yet.",
    refresh: "Refresh",
    none: "Not recorded",
    never: "Never",
    all: "All",
    status: "Status",
    created: "Created",
    updated: "Updated",
    actions: "Actions",
    enabled: "Enabled",
    disabled: "Disabled",
    active: "Active",
    inactive: "Inactive",
    secretTitle: "Save your secret now",
    secretValue: "Secret value",
    secretHint:
      "This value is shown once. Save it in your password manager. Leaving this page or hiding this tab clears it. If you lose an API key, revoke it and create another; rotate a lost webhook secret.",
    saved: "I have saved it",
    busy: "Updating…",
    future: "Choose a future expiry or leave it blank.",
    payments: "Payment settings",
    paymentHint:
      "Connect your store’s Razorpay account. Saving credentials does not initiate a payment.",
    keyId: "Razorpay key ID",
    keySecret: "Razorpay key secret",
    webhookSecret: "Razorpay webhook secret",
    mode: "Mode",
    test: "Test",
    live: "Live",
    connect: "Connect Razorpay",
    replace: "Replace credentials",
    disconnect: "Disconnect Razorpay",
    disconnectHint:
      "Prepaid orders that require this configuration will stop until you reconnect. Existing payment processing remains managed by the API.",
    notConnected: "Razorpay is not connected.",
    paymentSaved:
      "Payment credentials saved. The secret fields have been cleared.",
    maskedKey: "Saved key ID",
    inboundUrl: "Inbound webhook URL",
    setup: "Set up Razorpay callbacks",
    setupSteps: [
      "Choose the matching Test or Live mode in Razorpay, then open Accounts & Settings → Webhooks.",
      "Add a webhook and paste the inbound URL shown here. It must be publicly reachable; a local address cannot receive Razorpay callbacks.",
      "Use the same webhook secret you entered here. It is separate from your Razorpay API key secret.",
      "Select payment.captured, payment.failed, refund.processed and refund.failed, then save the webhook.",
    ],
    setupDocs: "Official Razorpay setup guide",
    replaceHint:
      "Enter the complete replacement credentials. Saved secrets are never loaded into these fields.",
    keys: "API keys",
    keyHint:
      "Give each integration only the permissions it needs. Secret values are shown once.",
    createKey: "Create API key",
    scopesLabel: "Permissions",
    expiry: "Expires (optional)",
    lastUsed: "Last used",
    prefix: "Public prefix",
    revoke: "Revoke key",
    revokeHint:
      "This integration key will stop authenticating immediately. Its secret cannot be recovered.",
    revoked: "Revoked",
    expired: "Expired",
    scopes: {
      "orders:read": "Read orders and their status",
      "orders:write": "Create and cancel orders",
      "catalog:read": "Read products and prices",
      "catalog:write": "Create and edit products",
      "inventory:read": "Read stock and warehouses",
      "inventory:write": "Create stock and adjust quantities",
    } as Record<string, string>,
    webhooks: "Outbound webhooks",
    webhookHint:
      "Send signed order updates to your own service. Payloads use the API’s safe projection.",
    createEndpoint: "Add endpoint",
    editEndpoint: "Edit endpoint",
    url: "Receiver URL",
    description: "Description (optional)",
    eventsLabel: "Events",
    targetHint:
      "Use a public HTTPS receiver without credentials or fragments in its URL. The server checks DNS and blocks private targets. HTTP/private targets are reserved for explicitly enabled local tests.",
    endpointActive: "Receive deliveries",
    deleteEndpoint: "Remove endpoint",
    deleteHint: "Stop future deliveries to this receiver.",
    rotate: "Rotate signing secret",
    rotateHint:
      "New deliveries use a new signing secret. Save the new value and update your receiver.",
    testPing: "Queue test ping",
    pingHint:
      "Queue a signed ping for this receiver. Check delivery status to see the result.",
    enable: "Re-enable endpoint",
    disable: "Disable endpoint",
    toggleHint:
      "Change whether this receiver accepts new deliveries. Re-enabling resets its consecutive-failure counter.",
    failureCount: "Consecutive failed attempts",
    disabledReason: "Disabled reason",
    failureThreshold: "Automatically disabled after repeated failed attempts.",
    ownerDisabled: "Disabled by the store owner.",
    deliveries: "Deliveries",
    attempts: "Attempts",
    httpStatus: "Last HTTP status",
    deliveryError: "Last error",
    nextAttempt: "Next attempt",
    eventType: "Event type",
    replayDelivery: "Replay delivery",
    replayDeliveryHint:
      "Requeue this saved event. Your receiver may process it again. The API rejects inactive or currently claimed deliveries.",
    verifyTitle: "Verify signatures in your receiver",
    verifyHint:
      "Verify the exact raw request bytes before parsing JSON. Check the timestamp, then parse verified JSON and deduplicate its signed id in durable storage. X-OOS-Delivery-Id is an unsigned diagnostic header. These secrets are separate from Razorpay’s webhook secret.",
    python: "Python",
    node: "Node.js",
    unknownEvent: "Unknown event",
    eventLabels: {
      "order.created": "Order received",
      "order.paid": "Order paid",
      "order.shipped": "Order shipped",
      "order.delivered": "Order delivered",
      "order.failed": "Order failed",
      "order.cancelled": "Order cancelled",
      "order.status_changed": "Any order status change",
      "payment.refunded": "Payment refunded",
      ping: "Test ping",
    } as Record<string, string>,
    notifications: "Notifications",
    notificationsHint:
      "Choose whether this store sends configured order notifications.",
    notificationsEnabled: "Send order notifications",
    notificationsSaved: "Notification preferences saved.",
    jobs: "Failed jobs",
    jobsHint:
      "Review technical failures before recovery. Private payloads and diagnostics are withheld.",
    task: "Task",
    queue: "Queue",
    retries: "Retries",
    jobDetail: "Failed job details",
    openJob: "Open job",
    replay: "Replay job",
    discard: "Discard job",
    replayHint:
      "Replay reopens only technically failed orders marked RETRIES_EXHAUSTED. For an order still processing, the API may requeue its saved event. Business failures and terminal orders are not reopened; pending refunds must finish first.",
    discardHint:
      "Mark this job discarded. This does not recover or cancel the order.",
    diagnostics: "Private diagnostics withheld.",
    jobErrors: {
      RETRIES_EXHAUSTED: "Processing exhausted its retry budget.",
      PROVIDER_UNAVAILABLE: "A provider was unavailable.",
      refund_failed:
        "Refund processing failed; review it before order recovery.",
      order_not_retryable: "The order cannot be reopened.",
    } as Record<string, string>,
    deliveryErrors: {
      transport_error:
        "The receiver could not be reached within the transport deadline.",
      http_status: "The receiver returned an unsuccessful HTTP status.",
      attempt_budget_exhausted: "Delivery attempts are exhausted.",
      unsafe_target: "The receiver failed the server’s safe-target checks.",
    } as Record<string, string>,
    tenants: "Platform tenants",
    tenantsHint: "Search store names or URL names and manage access.",
    search: "Store name or URL name",
    slug: "Store URL name",
    suspend: "Suspend store",
    activate: "Activate store",
    suspendHint:
      "Pause this store’s access and authenticated integrations. In-flight workflow recovery remains governed by the API.",
    activateHint: "Restore this store’s access and authenticated integrations.",
    reason: "Suspension reason",
    reasonUnavailable: "This API does not record a suspension reason.",
    statusLabels: {
      active: "Active",
      suspended: "Suspended",
      queued: "Queued",
      running: "Running",
      retrying: "Retrying",
      failed: "Failed",
      completed: "Completed",
      replayed: "Replayed",
      discarded: "Discarded",
      pending: "Pending",
      delivered: "Delivered",
      dead: "Attempts exhausted",
    } as Record<string, string>,
  },
  catalog: {
    title: "Catalog",
    hint: "Keep product details and prices ready for your next order.",
    create: "Create product",
    edit: "Edit product",
    search: "Search SKU or product name",
    activeFilter: "Product status",
    all: "All products",
    active: "Active",
    archived: "Archived",
    name: "Product name",
    description: "Description (optional)",
    price: "Price",
    priceHint: "Use up to two decimal places, for example 249.50.",
    currency: "Currency",
    immutable:
      "SKU and currency cannot be changed after creation. New products start active.",
    archive: "Archive product",
    unarchive: "Unarchive product",
    save: "Save product",
    empty: "No products match these filters.",
    saved: "Product saved.",
    invalidPrice:
      "Enter a nonnegative price with at most two decimals and ten whole-number digits.",
    duplicate: "That SKU already exists. Choose another SKU.",
    confirmArchive:
      "Archiving stops new orders from using this SKU. Existing orders keep their snapshots.",
    confirmUnarchive: "This SKU will become available for new orders.",
  },
  inventory: {
    title: "Inventory",
    hint: "See available and reserved stock in each warehouse.",
    warehouses: "Warehouses",
    warehouse: "Warehouse",
    createWarehouse: "Create warehouse",
    code: "Warehouse code",
    name: "Warehouse name",
    region: "Region",
    add: "Add inventory",
    available: "Available",
    reserved: "Reserved",
    threshold: "Low-stock threshold",
    low: "Low stock",
    adjust: "Adjust stock",
    delta: "Quantity change",
    reason: "Adjustment reason",
    adjustHint:
      "Use a positive number to add stock or a negative number to remove it. Reserved stock is unchanged.",
    result: (n: number) => `Available after adjustment: ${n}`,
    save: "Save adjustment",
    empty: "No inventory yet. Add a product and warehouse first.",
    warehousesEmpty: "No warehouses yet.",
    invalidDelta:
      "Enter a nonzero whole number that keeps available stock between 0 and 2,147,483,647.",
    reasonRequired: "Enter a reason for this stock change.",
    added: "Inventory saved.",
    adjusted: "Stock adjustment saved.",
    warehouseCreated: "Warehouse created.",
    choose: "Choose a warehouse",
    warehouseHelp: "Warehouse choices follow the current warehouse page below.",
    quantityInvalid: "Enter a whole number from 0 to 2,147,483,647.",
    codeConflict: "That warehouse code already exists.",
    duplicate:
      "Stock for this SKU and warehouse already exists. Adjust the existing row.",
  },
  orders: {
    title: "Orders",
    hint: "Follow each order from confirmation to delivery.",
    create: "Create test order",
    search: "Order ID or external reference",
    status: "Status",
    from: "From date",
    to: "To date",
    apply: "Apply filters",
    all: "All statuses",
    live: "Live",
    polling: "Reconnecting · refreshing every 15 seconds",
    reference: "External reference",
    total: "Total",
    previous: "Previous",
    next: "Next",
    page: (offset: number, total: number) =>
      `${offset + 1}–${Math.min(offset + 20, total)} of ${total}`,
    empty: "No orders match these filters.",
    detail: "Order details",
    buyer: "Buyer",
    items: "Items",
    sku: "SKU",
    quantity: "Quantity",
    payment: "Payment",
    prepaid: "Prepaid",
    cod: "Cash on delivery",
    cancel: "Cancel order",
    confirm: "Confirm",
    dismiss: "Back",
    reason: "Reason",
    cancelHint:
      "Cancellation releases reserved stock and requests any required refund. This cannot be undone.",
    timeline: "Order journey",
    historyEmpty: "No recorded events yet.",
    refunds: "Refunds",
    shipment: "Shipment",
    tracking: "Edit tracking",
    carrier: "Carrier",
    number: "Tracking number",
    url: "Carrier tracking URL",
    eta: "Estimated delivery",
    save: "Save",
    event: "Add shipment event",
    location: "Location (optional)",
    notes: "Notes (optional)",
    rotate: "Rotate tracking link",
    rotateHint: "The previous buyer link will immediately stop working.",
    copy: "Copy tracking URL",
    copied: "Tracking URL copied.",
    copyFailed: "Could not copy. Select and copy the displayed link.",
    name: "Buyer name (optional)",
    email: "Buyer email (optional)",
    phone: "Buyer phone (optional)",
    addItem: "Add item",
    remove: "Remove item",
    submitted: "Order accepted. Processing continues in the background.",
    expires: (seconds: number) =>
      seconds > 0
        ? `Payment window: ${seconds}s remaining`
        : "Payment window ended; awaiting the final provider outcome.",
    pendingAction: "Updating…",
    notFound: "Order not found",
    refresh: "Refresh",
    confirmation: "Review and confirm this change.",
    currency: "Currency",
    unknown: "Not provided",
    provider: "Provider",
    history: "Recorded activity",
    unitPrice: "Unit price",
    invalidDate: "End date must be on or after start date.",
    testHint:
      "This creates a real order in your store. Use test SKUs and mock payments for experiments.",
  },
  statuses: {
    collected: "Collected",
    pending_collection: "Awaiting cash collection",
    creating: "Creating payment",
    abandoned: "Abandoned",
    created: "Received",
    validated: "Validated",
    inventory_reserved: "Stock reserved",
    payment_pending: "Awaiting payment",
    paid: "Paid",
    fulfillment_requested: "Preparing shipment",
    shipped: "Shipped",
    delivered: "Delivered",
    cancelled: "Cancelled",
    failed: "Failed",
    pending: "Pending",
    in_transit: "Shipped / in transit",
    delayed: "Delayed",
    initiated: "Initiated",
    authorized: "Authorized",
    captured: "Captured",
    refunded: "Refunded",
    requested: "Requested",
    processing: "Processing",
    succeeded: "Succeeded",
  } as Record<string, string>,
  failures: {
    INSUFFICIENT_INVENTORY:
      "There was not enough available stock. The order could not proceed.",
    PAYMENT_DECLINED: "Payment was declined. Reserved stock is released.",
    PAYMENT_FAILED: "Payment failed. Reserved stock is released.",
    SHIPMENT_FAILED:
      "Shipment creation failed. Stock is released and any captured payment is queued for refund.",
    RETRIES_EXHAUSTED:
      "Processing could not finish after retries. Your store owner can review the failed job.",
    PAYMENT_EXPIRED:
      "Payment was not confirmed within the payment window. Stock is released.",
    LATE_PAYMENT:
      "Payment arrived after the allowed window. A refund is requested.",
    AMOUNT_MISMATCH:
      "The payment amount did not match this order. A refund is requested.",
    unknown:
      "The order could not complete. Review its payment, refund and shipment status before taking action.",
  } as Record<string, string>,
  dismiss: "Dismiss notice",

  brand: "Order Desk",

  tagline: "Your store, in order.",

  skip: "Skip to content",

  menu: "Open navigation",

  close: "Close navigation",

  account: "Account",

  logout: "Sign out",

  login: "Sign in",

  register: "Create your store",

  email: "Email address",

  password: "Password",

  owner: "Your name",

  business: "Store name",

  slug: "Store URL name (optional)",

  invite: "Invite code (if required)",

  welcome: "Welcome back",

  loginHint: "Sign in to manage your store.",

  registerHint: "A clear home for your orders and integrations.",

  pending: "Please wait…",

  submitRegister: "Create store",

  registered: "Your store is ready. Sign in to continue.",

  noAccount: "New seller?",

  hasAccount: "Already registered?",

  expired: "Your session ended. Please sign in again.",

  homeTitle: "Your workspace is ready",

  homeHint:
    "Manage your orders, products and warehouse stock from one workspace.",

  foundation: "Your workspace",

  emptyTitle: "A fresh start",

  emptyHint:
    "Choose Orders, Catalog or Inventory from the navigation to get started.",

  customerTitle: "Seller access required",

  customerHint:
    "This account is a buyer account. Sign in with your store’s owner or staff account to use the dashboard.",

  suspendedTitle: "Store access paused",

  suspendedHint:
    "Your store has been suspended. Contact the platform administrator for help.",

  platformTitle: "Platform workspace",

  platformHint:
    "You're signed in as a platform administrator. Tenant controls are coming soon.",

  errorTitle: "Something went wrong",

  errorHint: "We could not load this page. Please try again.",

  retry: "Try again",

  rateLimitTitle: "A short pause",

  retryAfter: (n: number) => `Try again in ${n} seconds.`,

  store: "Store",

  notBuilt: "This section is coming soon.",

  validation: {
    required: "Please complete this field.",
    phone: "Enter a valid phone number.",

    email: "Enter a valid email address.",

    password: "Use at least 8 characters.",

    short: "Use at least 2 characters.",
  },

  nav: {
    home: "Overview",

    orders: "Orders",

    catalog: "Catalog",

    inventory: "Inventory",

    payments: "Payments",

    "api-keys": "API keys",

    webhooks: "Webhooks",

    notifications: "Notifications",

    "failed-jobs": "Failed jobs",

    platform: "Platform",
  },

  errors: {
    credentials_unavailable:
      "This server cannot store or read provider credentials. Contact your administrator.",
    payment_not_configured:
      "Connect your payment provider before placing prepaid orders that require it.",
    unsafe_webhook_url:
      "Use a publicly reachable HTTPS receiver. Private, unsafe or unresolvable targets are blocked by the server.",
    job_already_replayed:
      "This job was already replayed. Refresh to see its current status.",
    order_not_retryable:
      "This order cannot be reopened. Business failures, cancelled or delivered orders are not retryable.",
    refund_pending:
      "A refund is still pending. Wait for it to finish before replaying this job.",
    product_sku_conflict: "That SKU already exists. Choose another SKU.",
    negative_stock:
      "Stock changed while you were editing. Refresh and enter a smaller reduction.",
    stock_out_of_range: "The adjustment exceeds the supported stock limit.",
    warehouse_code_conflict: "That warehouse code already exists.",
    inventory_conflict: "Inventory for this SKU and warehouse already exists.",
    product_required: "Create this SKU in the catalog before adding stock.",
    unknown_or_inactive_sku:
      "One or more SKUs are unknown or archived. Check the catalog.",
    currency_mismatch: "Product currencies must match the order currency.",
    not_found: "This record was not found.",
    conflict:
      "This change conflicts with the current record. Refresh and try again.",
    invalid_order_transition:
      "The order has moved on. Refresh before trying again.",
    idempotency_key_conflict:
      "This request key was already used for different order details.",
    insufficient_inventory: "There is not enough available stock.",
    unauthenticated: "Your session has ended. Please sign in.",

    forbidden: "Check your email and password, or contact your store owner.",

    tenant_suspended: "Store access has been paused.",

    registration_closed:
      "Store registration is closed. Contact the administrator for an invitation.",

    rate_limited: "Too many attempts. Please wait before trying again.",

    csrf_rejected: "Please reload the page and try again.",

    validation_error: "Please check the form and try again.",

    invalid_request: "Please check the request and try again.",

    dashboard_forbidden:
      "This account cannot access that part of the dashboard.",

    upstream_unavailable:
      "The service is temporarily unavailable. Please try again.",

    offline: "You appear to be offline. Check your connection and try again.",

    unknown: "We could not complete this request. Please try again.",
  } as Record<string, string>,
};
