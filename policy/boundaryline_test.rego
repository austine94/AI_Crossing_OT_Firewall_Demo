package boundaryline.authz_test

import data.boundaryline.authz
import rego.v1

test_monitoring_reader_allowed if {
    result := authz.decision with input as {
        "subject": {"id": "patch-agent-lab", "roles": ["monitoring-reader"]},
        "action": "read_patch_status",
        "resource": {"asset_id": "MON-SRV-001", "zone": "monitoring"},
        "scenario": "C",
    }
    result.allow
    result.reason == "MONITORING_READER_ALLOWED"
}

test_monitoring_reader_denied_for_critical if {
    result := authz.decision with input as {
        "subject": {"id": "patch-agent-lab", "roles": ["monitoring-reader"]},
        "action": "read_patch_status",
        "resource": {"asset_id": "PLC-CRIT-001", "zone": "critical"},
        "scenario": "C",
    }
    not result.allow
    result.reason == "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE"
}

test_critical_maintainer_allowed if {
    result := authz.decision with input as {
        "subject": {"id": "maintenance-service", "roles": ["critical-maintainer"]},
        "action": "read_patch_status",
        "resource": {"asset_id": "PLC-CRIT-001", "zone": "critical"},
        "scenario": "C",
    }
    result.allow
    result.reason == "CRITICAL_MAINTAINER_ALLOWED"
}

test_unknown_action_denied if {
    result := authz.decision with input as {
        "subject": {"id": "patch-agent-lab", "roles": ["monitoring-reader"]},
        "action": "write_controller_state",
        "resource": {"asset_id": "MON-SRV-001", "zone": "monitoring"},
        "scenario": "C",
    }
    not result.allow
    result.reason == "DEFAULT_DENY"
}
