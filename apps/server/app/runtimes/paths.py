"""Container paths shared by provisioning and the worker output protocol."""

HERMES_MOUNT = "/opt/data"
OPENCLAW_CONFIG_MOUNT = "/home/node/.openclaw"
WORKSPACE_MOUNTS = {
    "hermes": f"{HERMES_MOUNT}/workspace",
    "openclaw": f"{OPENCLAW_CONFIG_MOUNT}/workspace",
}
