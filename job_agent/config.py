"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — Gemini → Groq → Cerebras (failover on quota / expired keys)
    gemini_api_key: str = ""
    groq_api_key: str = ""
    cerebras_api_key: str = ""
    llm_provider_order: str = "gemini,groq,cerebras"
    llm_model: str = "gemini-3.5-flash-lite"  # primary Gemini model
    gemini_fallback_models: str = (
        "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.5-flash,gemini-3.8-flash"
    )
    # Groq free/dev: Llama 3.x is enterprise-only now; use gpt-oss (+ qwen preview)
    groq_models: str = (
        "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
    )
    cerebras_models: str = "llama3.1-8b,llama-3.3-70b,gpt-oss-120b"
    llm_min_interval_seconds: float = 45.0  # quality + quota; 30–60s recommended
    llm_max_retries: int = 3
    # Use Gemini and Groq (and Cerebras, when configured) at the same time.
    # Each provider keeps its own pace. Set false to try them one after another.
    llm_parallel_providers: bool = True


    # Job APIs
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    rapidapi_key: str = ""
    serpapi_key: str = ""
    remotive_enabled: bool = True
    arbeitnow_enabled: bool = True
    greenhouse_boards: str = ""
    lever_companies: str = ""
    ashby_orgs: str = ""
    smartrecruiters_companies: str = ""
    web_search_enabled: bool = True

    # Hunter.io — company people / email discovery (official API)
    # Free plan = 50 Domain Search credits / month — keep budgets tight
    hunter_api_key: str = ""
    hunter_enabled: bool = True
    hunter_monthly_budget: int = 45  # leave buffer under free-tier 50
    hunter_max_searches_per_run: int = 5  # hard cap per referral run (free tier)
    hunter_max_calls_per_company: int = 1  # 1 Domain Search = 1 credit
    hunter_only_when_needed: bool = True  # skip if curated already has 3+
    hunter_require_known_domain: bool = True  # skip tiny agencies with no domain map

    # Apify — LinkedIn jobs (cloud Actor; no LinkedIn login/CDP)
    apify_api_token: str = ""
    apify_linkedin_enabled: bool = False
    apify_linkedin_actor: str = "bebity/linkedin-jobs-scraper"
    apify_linkedin_published_at: str = "r86400"  # past 24 hours
    apify_linkedin_max_rows: int = 50

    # Database & paths
    database_url: str = "sqlite:///./data/job_agent.db"
    resume_path: str = "./data/resume/Abhishek_Android_Development_.pdf"
    master_resume_json: str = "./data/resume/master_resume.json"
    master_resume_tex: str = "./data/resume/master_android.tex"
    output_dir: str = "./output"
    log_dir: str = "./logs"

    # Search parameters
    daily_search_time: str = "07:00"
    timezone: str = "Asia/Kolkata"
    max_jobs_per_search: int = 200
    top_jobs_count: int = 0  # 0 = no cap; Excel gets every job at/above priority score
    # Minimum match score to include a job in the Excel export
    top_jobs_priority_score: int = 70
    job_recency_hours: int = 24
    resume_generation_threshold: int = 75
    # Also fetch JDs this far below the threshold — jobs with an empty JD score
    # low purely for lack of text and can clear the bar once enriched
    resume_enrich_score_margin: int = 15
    min_match_score: int = 0

    # Internet-wide discovery expansion
    resume_profiles_dir: str = "./data/resume/profiles"
    # false = do not search from any resume profile
    profile_based_search: bool = True
    active_profiles: str = ""  # empty = all folders under profiles/
    search_query_variants_per_profile: int = 10
    max_pages_per_source: int = 5
    max_results_per_source: int = 200
    max_job_age_days: int = 30
    max_company_career_discovery: int = 50
    request_delay_seconds: float = 1.0
    source_delay_seconds: float = 2.0
    enable_remoteok: bool = True
    enable_wwr: bool = True
    enable_himalayas: bool = True
    enable_ats_extra: bool = True
    enable_company_career_discovery: bool = True
    enable_career_portal_resolution: bool = True
    career_portal_timeout_seconds: float = 12.0
    career_portal_delay_seconds: float = 0.5
    career_portal_concurrency: int = 4
    cache_ttl_hours: int = 12
    job_locations: str = (
        "Mumbai,Thane,Navi Mumbai,Pune,Bangalore,Hyderabad,"
        "Chennai,Delhi NCR,India,Remote,Remote India"
    )
    # Target roles & locations (defaults, overridable via DB config)
    target_roles: List[str] = Field(default=[
        "Android Developer",
        "Android Engineer",
        "Android Software Engineer",
        "Mobile Developer",
        "Mobile Engineer",
        "Mobile Software Engineer",
        "Kotlin Developer",
        "Android Application Developer",
    ])
    target_locations_mumbai: List[str] = Field(default=[
        "Mumbai", "Navi Mumbai", "Thane", "Mumbai Metropolitan Region",
    ])
    target_locations_india: List[str] = Field(default=[
        "Bengaluru", "Bangalore", "Hyderabad", "Pune", "Delhi", "NCR",
        "Noida", "Gurgaon", "Gurugram", "Chennai", "Kolkata", "Ahmedabad",
        "Remote India", "India",
    ])

    # Notifications
    notification_enabled: bool = False
    notification_channel: str = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notification_email_to: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    slack_webhook_url: str = ""

    # Application automation
    auto_apply_enabled: bool = False
    require_approval_before_apply: bool = True

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Logging
    log_level: str = "INFO"

    # LaTeX
    latex_compiler: str = "tectonic"
    compile_pdf: bool = True
    # Tectonic/XeTeX breaks lines slightly wider than Overleaf's pdfTeX, so the
    # resume can spill onto page 2. Shrink and recompile until it fits.
    resume_max_pages: int = 1

    # Brave browser discovery (read-only job collection)
    brave_browser_enabled: bool = True
    brave_executable: str = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    brave_user_data_dir: str = "~/Library/Application Support/BraveSoftware/Brave-Browser"
    brave_profile_directory: str = "Profile 2"  # "Switch" profile
    brave_cdp_url: str = ""  # Optional: connect to running Brave via CDP
    brave_headless: bool = False
    brave_portals: str = "indeed,glassdoor,shine,wellfound,naukri"
    # 0 = one browser tab per site, all of those sites at once.
    # Set a positive number to cap how many sites run together.
    browser_parallel_tabs: int = 0
    # When CAPTCHA appears: pause so YOU can solve it in Brave (0 = skip portal immediately)
    brave_captcha_wait_seconds: int = 120

    # LinkedIn CDP scraping removed from job search. Use scripts/run_referral_agent.py
    linkedin_outreach_enabled: bool = False
    linkedin_outreach_threshold: int = 75
    linkedin_max_contacts_per_job: int = 3

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def greenhouse_board_list(self) -> List[str]:
        return [b.strip() for b in self.greenhouse_boards.split(",") if b.strip()]

    @property
    def lever_company_list(self) -> List[str]:
        return [c.strip() for c in self.lever_companies.split(",") if c.strip()]

    @property
    def ashby_orgs_list(self) -> List[str]:
        return [o.strip() for o in self.ashby_orgs.split(",") if o.strip()]

    @property
    def smartrecruiters_companies_list(self) -> List[str]:
        return [c.strip() for c in self.smartrecruiters_companies.split(",") if c.strip()]

    @property
    def brave_portals_list(self) -> List[str]:
        return [p.strip() for p in self.brave_portals.split(",") if p.strip()]

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    def ensure_dirs(self) -> None:
        for d in [self.data_dir, self.data_dir / "resume", Path(self.output_dir), Path(self.log_dir)]:
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
