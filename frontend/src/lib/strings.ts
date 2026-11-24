export const strings = {
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
