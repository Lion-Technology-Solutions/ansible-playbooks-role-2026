"""Allowlisted provider setup metadata; no arbitrary API URLs or scopes."""

def field(key,label,secret=True,required=True,help=''):
    return dict(key=key,label=label,secret=secret,required=required,help=help)

def provider(name,category,glyph,description,fields,url,guide,capability):
    return dict(name=name,category=category,glyph=glyph,description=description,fields=fields,url=url,guide=guide,capability=capability)

CATALOG={
 'google_signin':provider('Google Sign-in','security','G','Google account sign-up and sign-in.',[
  field('GOOGLE_SIGNIN_CLIENT_ID','Google OAuth client ID',False),field('GOOGLE_SIGNIN_CLIENT_SECRET','Google OAuth client secret')],
  'https://console.cloud.google.com/apis/credentials','Create a Web application OAuth client. Register https://www.liontechacademy.com/auth/google/callback exactly. Use openid, email and profile scopes, then enable Google in Sign-in settings. Calendar credentials remain separate.','OpenID Connect identity verification; portal email and MFA policy still applies'),
 'facebook_signin':provider('Facebook Sign-in','security','f','Facebook account sign-up and sign-in.',[
  field('FACEBOOK_SIGNIN_CLIENT_ID','Meta application ID',False),field('FACEBOOK_SIGNIN_CLIENT_SECRET','Meta application secret')],
  'https://developers.facebook.com/apps','Configure Facebook Login for Web and register https://www.liontechacademy.com/auth/facebook/callback. Enable email/public_profile and complete any required Meta review, privacy policy and data-deletion settings. Enable Facebook in Sign-in settings.','Validated app-scoped identity; independent email verification and portal MFA'),
 'sms_verify':provider('SMS OTP · Twilio Verify','security','W','Phone verification and two-factor SMS codes.',[
  field('VERIFY_ACCOUNT_SID','Twilio account SID',False),field('VERIFY_AUTH_TOKEN','Twilio auth token'),field('VERIFY_SERVICE_SID','Verify service SID (VA...)',False)],
  'https://console.twilio.com/us1/develop/verify/services','Create a Twilio Verify service with SMS enabled. Configure your permitted destinations, fraud guard, account billing and any required trial-number verification. Account authentication does not prove SMS delivery to every country.','Student-initiated SMS enrollment and login challenges; verified codes required'),
 'openai':provider('ChatGPT · OpenAI','ai','✦','AI student support and reviewed admin updates.',[
  field('OPENAI_API_KEY','OpenAI project API key'),field('OPENAI_MODEL','Responses API model',False,True,'Recommended: gpt-5.4-mini. Use a model available to your OpenAI project.')],
  'https://platform.openai.com/api-keys','Create a restricted project API key with Responses and model-read access. Configure project billing and spending limits in OpenAI. Save and test here, then open AI Assistant. Your ChatGPT sign-in does not provide an API key.','Student inquiries; editable program, curriculum, lesson, announcement and live-class drafts. Admin review is required to apply changes'),
 'stripe':provider('Stripe','payments','S','Card payments linked to enrollment invoices.',[
  field('STRIPE_SECRET_KEY','Secret API key'),field('STRIPE_WEBHOOK_SECRET','Webhook signing secret')],
  'https://dashboard.stripe.com/apikeys','Use a Stripe sandbox first. Register the webhook shown below for checkout.session.completed and checkout.session.async_payment_succeeded, using snapshot events.','Hosted checkout and verified payment activation'),
 'flutterwave':provider('Flutterwave','payments','F','Configure your Flutterwave merchant account.',[
  field('FLW_SECRET_KEY','V3 secret API key'),field('FLW_WEBHOOK_SECRET','V3 webhook secret hash'),field('FLW_CHECKOUT_CURRENCY','Enrollment checkout currency (default USD)',False,False,'Use a currency enabled for your Flutterwave merchant account, such as USD, NGN, GHS or XAF. Non-USD checkout requires a current portal exchange rate.')],
  'https://dashboard.flutterwave.com','Use V3 credentials. In Flutterwave Webhooks, switch to V3, enter the exact URL below and use the same random secret hash in both systems. Test keys never grant real class access. Merchant verification and supported currencies remain provider requirements.','Invoice-linked enrollment-fee checkout and server-verified V3 payment notifications; tuition payment links remain separate'),
 'zoom':provider('Zoom','teaching','classes','Schedule and update live Zoom meetings.',[
  field('ZOOM_ACCOUNT_ID','Account ID',False),field('ZOOM_CLIENT_ID','Client ID',False),field('ZOOM_CLIENT_SECRET','Client secret'),field('ZOOM_HOST_USER','Host email or user ID',False)],
  'https://marketplace.zoom.us','Create and activate a Server-to-Server OAuth app. Enable meeting create/update and host user-read permissions. Choose a host licensed for your class requirements.','Live class scheduling and existing-meeting updates'),
 'google':provider('Google Calendar','teaching','G','Students authorize their own class calendar.',[
  field('GOOGLE_CLIENT_ID','OAuth client ID',False),field('GOOGLE_CLIENT_SECRET','OAuth client secret')],
  'https://console.cloud.google.com/apis/credentials','Enable Google Calendar API. Create a Web application OAuth client and register the exact callback below. Configure consent for calendar.app.created; complete Google verification where required.','Student-authorized LionTech calendar connection and event sync'),
 'microsoft':provider('Microsoft 365','microsoft','M','Connect students’ Microsoft calendars.',[
  field('MICROSOFT_CLIENT_ID','Application / client ID',False),field('MICROSOFT_CLIENT_SECRET','Client secret value'),field('MICROSOFT_TENANT','Tenant ID or common',False,True,'Use common for personal and work accounts when your app registration permits them.')],
  'https://portal.azure.com','Create a Web app registration supporting your intended account types. Register the callback below and delegated Calendars.ReadWrite and offline_access permissions.','Student calendar connection and sync; Teams, OneDrive and Microsoft sign-in not enabled'),
 'email':provider('Email · SendGrid','communication','announcements','Class-update emails and calendar invitations.',[
  field('SENDGRID_API_KEY','SendGrid API key'),field('SENDGRID_FROM_EMAIL','Verified sender email',False)],
  'https://app.sendgrid.com/settings/api_keys','Authenticate your sending domain or verify the sender in SendGrid. The API key needs Mail Send; connection validation reads its available scopes.','Direct class-update email delivery'),
 'whatsapp':provider('WhatsApp Business','communication','W','Configure Twilio WhatsApp credentials.',[
  field('TWILIO_ACCOUNT_SID','Twilio account SID',False),field('TWILIO_AUTH_TOKEN','Twilio auth token'),field('TWILIO_WHATSAPP_FROM','WhatsApp sender',False,True,'Example format: whatsapp:+19152219465'),field('TWILIO_TEMPLATE_SID','Approved support account-review template SID',False,False),field('TWILIO_CLASS_TEMPLATE_SID','Approved student class-update template SID',False,False,'Text template variables: 1 student number, 2 class title, 3 class date/time and timezone, 4 secure portal class URL. Separate from the support account-review template.'),field('TWILIO_TUITION_TEMPLATE_SID','Approved student tuition-status template SID',False,False,'Text template variables: 1 student number, 2 tuition status, 3 program name, 4 payment review URL. Requires student payment-alert consent.')],
  'https://console.twilio.com','Register an approved WhatsApp sender and templates in Twilio. Account validation does not prove sender/template approval or recipient consent.','Account validation and approved-template support review notifications; student WhatsApp OTP is separate'),
 'facebook':provider('Facebook','social','f','Configure your Meta application and Page access.',[
  field('FACEBOOK_APP_ID','Meta app ID',False),field('FACEBOOK_APP_SECRET','Meta app secret'),field('FACEBOOK_PAGE_ID','Facebook Page ID',False,False),field('FACEBOOK_PAGE_TOKEN','Page access token',True,False)],
  'https://developers.facebook.com/apps','Create a Meta app. Page tools require the relevant permissions, Page authorization and any required Meta review.','App / Page authentication validation; social sign-in, leads and publishing not enabled'),
 'youtube':provider('YouTube','social','play','Validate access to your public course channel.',[
  field('YOUTUBE_API_KEY','YouTube Data API key'),field('YOUTUBE_CHANNEL_ID','Channel ID',False)],
  'https://console.cloud.google.com/apis/credentials','Enable YouTube Data API v3. Restrict the API key to that API. Use a channel ID beginning with UC; public channel access does not grant upload access.','Public channel access validation; upload and playlist automation not enabled'),
 'instagram':provider('Instagram','social','I','Configure an authorized Instagram business account.',[
  field('INSTAGRAM_BUSINESS_ID','Instagram professional account ID',False),field('INSTAGRAM_ACCESS_TOKEN','Authorized business access token')],
  'https://developers.facebook.com/apps','Use an eligible professional account and authorized Meta access token with account-read permissions. Publishing requires additional permissions and review.','Account authentication validation; publishing not enabled'),
 'linkedin':provider('LinkedIn','social','in','Configure LinkedIn application credentials.',[
  field('LINKEDIN_CLIENT_ID','OAuth client ID',False),field('LINKEDIN_CLIENT_SECRET','OAuth client secret'),field('LINKEDIN_ACCESS_TOKEN','Authorized access token',True,False)],
  'https://www.linkedin.com/developers/apps','Enable the appropriate LinkedIn product and obtain user consent. An optional token with openid/profile can be tested against userinfo.','Authorized profile validation; professional sign-in and sharing not enabled'),
}
CATEGORIES={'all':'All','payments':'Payments','teaching':'Teaching','communication':'Communication','microsoft':'Microsoft','social':'Social','ai':'AI Assistant','security':'Sign-in & Security'}
ENV_PROVIDER={item['key']:slug for slug,entry in CATALOG.items() for item in entry['fields']}
