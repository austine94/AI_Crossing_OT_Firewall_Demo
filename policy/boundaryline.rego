package boundaryline.authz

import rego.v1

policy_version := "1.0.0"

default decision := {
    "allow": false,
    "reason": "DEFAULT_DENY",
    "policy_version": "1.0.0",
}

has_role(role) if {
    input.subject.roles[_] == role
}

decision := {
    "allow": true,
    "reason": "MONITORING_READER_ALLOWED",
    "policy_version": policy_version,
} if {
    input.action == "read_patch_status"
    input.resource.zone == "monitoring"
    has_role("monitoring-reader")
}

decision := {
    "allow": true,
    "reason": "CRITICAL_MAINTAINER_ALLOWED",
    "policy_version": policy_version,
} if {
    input.action == "read_patch_status"
    input.resource.zone == "critical"
    has_role("critical-maintainer")
}

decision := {
    "allow": false,
    "reason": "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE",
    "policy_version": policy_version,
} if {
    input.action == "read_patch_status"
    input.resource.zone == "critical"
    not has_role("critical-maintainer")
}
