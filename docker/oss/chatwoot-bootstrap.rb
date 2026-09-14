# frozen_string_literal: true

require 'json'
require 'zlib'

EXPECTED_VERSION = '4.15.1'
MARKER = 'klm-hub-local-v1'
SUBSCRIPTIONS = %w[
  contact_created
  contact_updated
  conversation_created
  conversation_updated
  conversation_status_changed
  message_created
  message_updated
].freeze

def required_env(name)
  value = ENV.fetch(name, '').strip
  abort("missing required local bootstrap variable: #{name}") if value.empty?
  value
end

def marker_from(value)
  (value || {})['klm_bootstrap_key']
end

version = Rails.root.join('VERSION_CW').read.strip
abort("unsupported Chatwoot version #{version.inspect}; expected #{EXPECTED_VERSION}") unless version == EXPECTED_VERSION

account_id = Integer(required_env('KLM_OSS_CHATWOOT_ACCOUNT_ID'), 10)
user_id = Integer(required_env('KLM_OSS_CHATWOOT_USER_ID'), 10)
inbox_id = Integer(required_env('KLM_OSS_CHATWOOT_INBOX_ID'), 10)
admin_name = required_env('KLM_OSS_CHATWOOT_ADMIN_NAME')
admin_email = required_env('KLM_OSS_CHATWOOT_ADMIN_EMAIL').downcase
admin_password = required_env('KLM_OSS_CHATWOOT_ADMIN_PASSWORD')
api_token = required_env('KLM_OSS_CHATWOOT_API_ACCESS_TOKEN')
webhook_secret = required_env('KLM_OSS_CHATWOOT_WEBHOOK_SECRET')
workspace_id = ENV.fetch('KLM_OSS_KLM_WORKSPACE_ID', '').strip

result = {}

ActiveRecord::Base.transaction do
  ActiveRecord::Base.connection.execute(
    "SELECT pg_advisory_xact_lock(#{Zlib.crc32(MARKER)})"
  )

  account = Account.find_by(id: account_id)
  if account && marker_from(account.custom_attributes) != MARKER
    abort("Chatwoot account ID #{account_id} is already owned by another installation")
  end
  account ||= Account.create!(
    id: account_id,
    name: 'KLM Advogados',
    locale: 'pt_BR',
    custom_attributes: { 'klm_bootstrap_key' => MARKER }
  )
  account.update!(
    name: 'KLM Advogados',
    custom_attributes: (account.custom_attributes || {}).merge(
      'klm_bootstrap_key' => MARKER,
      'klm_workspace_id' => workspace_id.presence
    ).compact.except('onboarding_step')
  )
  Current.account = account

  user = User.find_by(email: admin_email)
  if user && marker_from(user.custom_attributes) != MARKER
    abort("Chatwoot admin email #{admin_email} is already owned by another installation")
  end
  if user && user.id != user_id
    abort("Chatwoot admin email #{admin_email} has unexpected ID #{user.id}")
  end
  unless user
    user = User.new(
      id: user_id,
      name: admin_name,
      email: admin_email,
      password: admin_password,
      password_confirmation: admin_password,
      type: 'SuperAdmin',
      custom_attributes: { 'klm_bootstrap_key' => MARKER }
    )
    user.skip_confirmation_notification!
    user.confirmed_at = Time.current
    user.save!
  end
  abort('stored Chatwoot admin password differs from the protected local environment') unless user.valid_password?(admin_password)
  user.update!(
    name: admin_name,
    custom_attributes: (user.custom_attributes || {}).merge('klm_bootstrap_key' => MARKER)
  )

  account_user = AccountUser.find_or_initialize_by(account: account, user: user)
  account_user.role = :administrator
  account_user.save!

  access_token = user.access_token || user.create_access_token
  conflicting_token = AccessToken.where(token: api_token).where.not(id: access_token.id).exists?
  abort('configured Chatwoot API token collides with another owner') if conflicting_token
  access_token.update!(token: api_token) unless access_token.token == api_token

  channel = Channel::Api.where(account_id: account.id)
                         .find_by("additional_attributes ->> 'klm_bootstrap_key' = ?", MARKER)
  channel ||= Channel::Api.create!(
    account: account,
    webhook_url: nil,
    additional_attributes: { 'klm_bootstrap_key' => MARKER }
  )
  channel.update!(
    webhook_url: nil,
    additional_attributes: (channel.additional_attributes || {}).merge(
      'klm_bootstrap_key' => MARKER,
      'klm_workspace_id' => workspace_id.presence
    ).compact
  )

  inbox = Inbox.find_by(id: inbox_id)
  if inbox && inbox.channel != channel
    abort("Chatwoot inbox ID #{inbox_id} is already owned by another channel")
  end
  inbox ||= Inbox.create!(
    id: inbox_id,
    account: account,
    channel: channel,
    name: 'Atendimento KLM',
    timezone: ENV.fetch('KLM_OSS_TIMEZONE', 'America/Sao_Paulo'),
    enable_auto_assignment: false
  )
  inbox.update!(name: 'Atendimento KLM', enable_auto_assignment: false)
  InboxMember.find_or_create_by!(inbox: inbox, user: user)

  if workspace_id.present?
    webhook_url = "http://chatwoot-gateway:8080/__klm_bridge/api/v1/oss-adapters/chatwoot/webhooks/#{workspace_id}"
    webhook = Webhook.find_or_initialize_by(account: account, url: webhook_url)
    if webhook.persisted? && webhook.secret != webhook_secret
      abort('stored Chatwoot webhook secret differs from the protected local environment')
    end
    webhook.name = 'KLM Hub local bridge'
    webhook.secret = webhook_secret
    webhook.subscriptions = SUBSCRIPTIONS
    webhook.webhook_type = :account_type
    webhook.save!
    result[:webhook_id] = webhook.id
  end

  Redis::Alfred.delete(Redis::Alfred::CHATWOOT_INSTALLATION_ONBOARDING)

  %w[accounts users inboxes].each do |table|
    sequence = ActiveRecord::Base.connection.select_value(
      "SELECT pg_get_serial_sequence('#{table}', 'id')"
    )
    next if sequence.blank?

    ActiveRecord::Base.connection.execute(
      "SELECT setval(#{ActiveRecord::Base.connection.quote(sequence)}, " \
      "GREATEST((SELECT COALESCE(MAX(id), 1) FROM #{table}), 1), true)"
    )
  end

  result.merge!(
    account_id: account.id,
    user_id: user.id,
    inbox_id: inbox.id,
    channel_id: channel.id,
    workspace_id: workspace_id.presence,
    version: version
  )
end

puts JSON.generate(result)
