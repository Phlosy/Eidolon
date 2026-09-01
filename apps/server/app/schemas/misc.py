from pydantic import BaseModel


class SettingsOut(BaseModel):
    runtime_mode: str
    workspace_root: str
    api_host: str
    api_port: int
    web_port: int
    company_name: str
