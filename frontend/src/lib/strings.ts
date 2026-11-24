export const strings = {
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
    "You're securely signed in to your store. More tools for your daily orders are coming soon.",

  foundation: "Your workspace",

  emptyTitle: "A fresh start",

  emptyHint: "There is nothing to show here yet.",

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
