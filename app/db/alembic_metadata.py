"""Exact compatibility metadata used by Alembic autogeneration.

Published migrations predate SQLAlchemy's automatic ``index=True`` names for
some models.  The database index definitions are correct; only their names
differ from the current in-memory metadata.  Alembic compares indexes by name,
so without this explicit alignment it proposes destructive drop/create pairs.

The map below is intentionally strict.  Every entry includes the expected
column tuple and uniqueness flag.  A model change that alters either shape
fails fast instead of silently hiding real schema drift.
"""

from __future__ import annotations

from sqlalchemy import MetaData

LEGACY_INDEX_METADATA_ALIGNMENT: dict[tuple[str, str], tuple[str, tuple[str, ...], bool]] = {
    ("case_facts", "ix_case_facts_is_controverted"): (
        "ix_case_fact_is_controverted",
        ("is_controverted",),
        False,
    ),
    ("case_facts", "ix_case_facts_legal_demand_id"): (
        "ix_case_fact_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("case_facts", "ix_case_facts_workspace_id"): (
        "ix_case_fact_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("case_parties", "ix_case_parties_legal_demand_id"): (
        "ix_case_party_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("case_parties", "ix_case_parties_pole"): (
        "ix_case_party_pole",
        ("pole",),
        False,
    ),
    ("case_parties", "ix_case_parties_workspace_id"): (
        "ix_case_party_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("case_pleadings", "ix_case_pleadings_legal_demand_id"): (
        "ix_case_pleading_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("case_pleadings", "ix_case_pleadings_workspace_id"): (
        "ix_case_pleading_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("case_proof_links", "ix_case_proof_links_document_id"): (
        "ix_case_proof_link_document_id",
        ("document_id",),
        False,
    ),
    ("case_proof_links", "ix_case_proof_links_fact_id"): (
        "ix_case_proof_link_fact_id",
        ("fact_id",),
        False,
    ),
    ("case_proof_links", "ix_case_proof_links_legal_demand_id"): (
        "ix_case_proof_link_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("case_proof_links", "ix_case_proof_links_status"): (
        "ix_case_proof_link_status",
        ("status",),
        False,
    ),
    ("case_proof_links", "ix_case_proof_links_workspace_id"): (
        "ix_case_proof_link_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("distribution_decisions", "ix_distribution_decisions_legal_demand_id"): (
        "ix_distribution_decision_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("distribution_decisions", "ix_distribution_decisions_trigger"): (
        "ix_distribution_decision_trigger",
        ("trigger",),
        False,
    ),
    ("distribution_decisions", "ix_distribution_decisions_workspace_id"): (
        "ix_distribution_decision_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("distribution_rules", "ix_distribution_rules_active"): (
        "ix_distribution_rule_active",
        ("active",),
        False,
    ),
    ("distribution_rules", "ix_distribution_rules_workspace_id"): (
        "ix_distribution_rule_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("document_asset_references", "ix_document_asset_references_workspace_id"): (
        "ix_document_asset_reference_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("document_assets", "ix_document_assets_workspace_id"): (
        "ix_document_asset_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("document_share_downloads", "ix_document_share_downloads_workspace_id"): (
        "ix_document_share_download_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("document_share_links", "ix_document_share_links_workspace_id"): (
        "ix_document_share_link_workspace_id",
        ("workspace_id",),
        False,
    ),
    (
        "klm_legal_artifact_versions",
        "ix_klm_legal_artifact_versions_artifact_key",
    ): ("ix_klm_legal_artifact_artifact_key", ("artifact_key",), False),
    (
        "klm_legal_artifact_versions",
        "ix_klm_legal_artifact_versions_created_by_user_id",
    ): ("ix_klm_legal_artifact_created_by_user_id", ("created_by_user_id",), False),
    (
        "klm_legal_artifact_versions",
        "ix_klm_legal_artifact_versions_legal_demand_id",
    ): ("ix_klm_legal_artifact_version_legal_demand_id", ("legal_demand_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_chain_id",
    ): ("ix_klm_message_evidence_chain_id", ("chain_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_channel_event_id",
    ): ("ix_klm_message_evidence_channel_event_id", ("channel_event_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_connection_id",
    ): ("ix_klm_message_evidence_connection_id", ("connection_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_envelope_id",
    ): ("ix_klm_message_evidence_envelope_id", ("envelope_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_event_type",
    ): ("ix_klm_message_evidence_event_type", ("event_type",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_legacy_whatsapp_message_id",
    ): (
        "ix_klm_message_evidence_legacy_whatsapp_message_id",
        ("legacy_whatsapp_message_id",),
        False,
    ),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_message_id",
    ): ("ix_klm_message_evidence_message_id", ("message_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_previous_event_id",
    ): ("ix_klm_message_evidence_previous_event_id", ("previous_event_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_provider",
    ): ("ix_klm_message_evidence_provider", ("provider",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_received_at",
    ): ("ix_klm_message_evidence_received_at", ("received_at",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_storage_object_id",
    ): ("ix_klm_message_evidence_storage_object_id", ("storage_object_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_thread_id",
    ): ("ix_klm_message_evidence_thread_id", ("thread_id",), False),
    (
        "klm_omnichannel_message_evidence_events",
        "ix_klm_omnichannel_message_evidence_events_workspace_id",
    ): ("ix_klm_message_evidence_workspace_id", ("workspace_id",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_actor_user_id",
    ): ("ix_klm_provider_media_recon_actor", ("actor_user_id",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_connection_id",
    ): ("ix_klm_provider_media_recon_connection", ("connection_id",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_decision",
    ): ("ix_klm_provider_media_recon_decision", ("decision",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_resolved_at",
    ): ("ix_klm_provider_media_recon_resolved", ("resolved_at",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_upload_intent_id",
    ): ("ix_klm_provider_media_recon_intent", ("upload_intent_id",), False),
    (
        "klm_omnichannel_provider_media_upload_reconciliations",
        "ix_klm_omnichannel_provider_media_upload_reconciliations_workspace_id",
    ): ("ix_klm_provider_media_recon_workspace", ("workspace_id",), False),
    (
        "klm_omnichannel_provider_webhook_envelopes",
        "ix_klm_omnichannel_provider_webhook_envelopes_provider",
    ): ("ix_klm_provider_envelope_provider", ("provider",), False),
    (
        "klm_omnichannel_provider_webhook_envelopes",
        "ix_klm_omnichannel_provider_webhook_envelopes_received_at",
    ): ("ix_klm_provider_envelope_received_at", ("received_at",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_canonical_message_id",
    ): ("ix_klm_sched_evt_message", ("canonical_message_id",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_connection_id",
    ): ("ix_klm_sched_evt_connection", ("connection_id",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_event_type",
    ): ("ix_klm_sched_evt_type", ("event_type",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_occurred_at",
    ): ("ix_klm_sched_evt_occurred", ("occurred_at",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_provider",
    ): ("ix_klm_sched_evt_provider", ("provider",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_received_at",
    ): ("ix_klm_sched_evt_received", ("received_at",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_scheduled_message_id",
    ): ("ix_klm_sched_evt_schedule", ("scheduled_message_id",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_thread_id",
    ): ("ix_klm_sched_evt_thread", ("thread_id",), False),
    (
        "klm_omnichannel_scheduled_message_events",
        "ix_klm_omnichannel_scheduled_message_events_workspace_id",
    ): ("ix_klm_sched_evt_workspace", ("workspace_id",), False),
    (
        "klm_process_monitoring_records",
        "ix_klm_process_monitoring_records_created_by_user_id",
    ): ("ix_klm_process_created_by_user_id", ("created_by_user_id",), False),
    (
        "legal_citation_verifications",
        "ix_legal_citation_verifications_artifact_version_id",
    ): (
        "ix_legal_citation_verification_artifact_version_id",
        ("artifact_version_id",),
        False,
    ),
    (
        "legal_citation_verifications",
        "ix_legal_citation_verifications_legal_demand_id",
    ): ("ix_legal_citation_verification_legal_demand_id", ("legal_demand_id",), False),
    ("legal_citation_verifications", "ix_legal_citation_verifications_status"): (
        "ix_legal_citation_verification_status",
        ("status",),
        False,
    ),
    (
        "legal_citation_verifications",
        "ix_legal_citation_verifications_workspace_id",
    ): ("ix_legal_citation_verification_workspace_id", ("workspace_id",), False),
    ("legal_exceptions", "ix_legal_exceptions_exception_kind"): (
        "ix_legal_exception_exception_kind",
        ("exception_kind",),
        False,
    ),
    ("legal_exceptions", "ix_legal_exceptions_matter_id"): (
        "ix_legal_exception_matter_id",
        ("matter_id",),
        False,
    ),
    ("legal_exceptions", "ix_legal_exceptions_priority"): (
        "ix_legal_exception_priority",
        ("priority",),
        False,
    ),
    ("legal_exceptions", "ix_legal_exceptions_status"): (
        "ix_legal_exception_status",
        ("status",),
        False,
    ),
    ("legal_exceptions", "ix_legal_exceptions_workspace_id"): (
        "ix_legal_exception_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("legal_review_findings", "ix_legal_review_findings_artifact_version_id"): (
        "ix_legal_review_finding_artifact_version_id",
        ("artifact_version_id",),
        False,
    ),
    ("legal_review_findings", "ix_legal_review_findings_legal_demand_id"): (
        "ix_legal_review_finding_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("legal_review_findings", "ix_legal_review_findings_severity"): (
        "ix_legal_review_finding_severity",
        ("severity",),
        False,
    ),
    ("legal_review_findings", "ix_legal_review_findings_workspace_id"): (
        "ix_legal_review_finding_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("monitored_processes", "ix_monitored_processes_matter_id"): (
        "ix_monitored_process_matter_id",
        ("matter_id",),
        False,
    ),
    ("monitored_processes", "ix_monitored_processes_workspace_id"): (
        "ix_monitored_process_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("process_publications", "ix_process_publications_fonte"): (
        "ix_process_publication_fonte",
        ("fonte",),
        False,
    ),
    ("process_publications", "ix_process_publications_workspace_id"): (
        "ix_process_publication_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("protocol_folders", "ix_protocol_folders_artifact_version_id"): (
        "ix_protocol_folder_artifact_version_id",
        ("artifact_version_id",),
        False,
    ),
    ("protocol_folders", "ix_protocol_folders_legal_demand_id"): (
        "ix_protocol_folder_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("protocol_folders", "ix_protocol_folders_matter_id"): (
        "ix_protocol_folder_matter_id",
        ("matter_id",),
        False,
    ),
    ("protocol_folders", "ix_protocol_folders_state"): (
        "ix_protocol_folder_state",
        ("state",),
        False,
    ),
    ("protocol_folders", "ix_protocol_folders_workspace_id"): (
        "ix_protocol_folder_workspace_id",
        ("workspace_id",),
        False,
    ),
    ("sla_escalation_logs", "ix_sla_escalation_logs_legal_demand_id"): (
        "ix_sla_escalation_log_legal_demand_id",
        ("legal_demand_id",),
        False,
    ),
    ("sla_escalation_logs", "ix_sla_escalation_logs_level"): (
        "ix_sla_escalation_log_level",
        ("level",),
        False,
    ),
    ("sla_escalation_logs", "ix_sla_escalation_logs_workspace_id"): (
        "ix_sla_escalation_log_workspace_id",
        ("workspace_id",),
        False,
    ),
    (
        "super_admin_notice_audit_events",
        "ix_super_admin_notice_audit_events_occurred_at",
    ): ("ix_super_admin_notice_audit_occurred", ("occurred_at",), False),
    ("support_transcript_emails", "ix_support_transcript_emails_ticket_id"): (
        "ix_support_transcript_email_ticket_id",
        ("ticket_id",),
        False,
    ),
    ("support_transcript_emails", "ix_support_transcript_emails_workspace_id"): (
        "ix_support_transcript_email_workspace_id",
        ("workspace_id",),
        False,
    ),
}


def align_published_index_names(metadata: MetaData) -> None:
    """Align only verified naming-only differences with published migrations."""

    for (table_name, metadata_name), (
        published_name,
        expected_columns,
        expected_unique,
    ) in LEGACY_INDEX_METADATA_ALIGNMENT.items():
        table = metadata.tables.get(table_name)
        if table is None:
            raise RuntimeError(f"Alembic metadata table is missing: {table_name}")
        matches = [
            index for index in table.indexes if index.name in {metadata_name, published_name}
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one compatible index {table_name}.{metadata_name}; "
                f"found {len(matches)}"
            )
        index = matches[0]
        actual_columns = tuple(column.name for column in index.columns)
        actual_unique = bool(index.unique)
        if actual_columns != expected_columns or actual_unique != expected_unique:
            raise RuntimeError(
                f"Legacy index shape changed for {table_name}.{metadata_name}: "
                f"columns={actual_columns!r}, unique={actual_unique!r}"
            )
        index.name = published_name
