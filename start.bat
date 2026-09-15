@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv (
    echo No venv found - run setup.bat first.
    exit /b 1
)
call venv\Scripts\activate.bat

echo Job Discovery
echo 1^) Run full pipeline on a URL (discover -^> scrape -^> normalize -^> match -^> AI filter)
echo 2^) Open web dashboard
echo 3^) Add a criteria profile
echo 4^) List criteria profiles
echo 5^) Discover job URLs from a site (no saving)
echo 6^) Add a source
echo 7^) List sources
echo 8^) Scrape a source
echo 9^) Normalize scraped jobs
echo 10^) Match jobs against a criteria profile (static rules)
echo 11^) Re-score with AI (skipped if no AI_API_KEY is set)
echo q^) Quit
set /p choice=^>

if "%choice%"=="1" (
    set /p url=Website URL:
    set /p criteria_label=Criteria label (optional, e.g. 'default'):
    set /p skip_ai=Skip AI filtering? [y/N]:
    set "args=!url!"
    if not "!criteria_label!"=="" set "args=!args! --criteria !criteria_label!"
    if /i "!skip_ai!"=="y" set "args=!args! --no-ai"
    python discover.py !args!
) else if "%choice%"=="2" (
    echo Starting web UI - open http://127.0.0.1:5000 in your browser (Ctrl+C to stop)
    python -m webui.app
) else if "%choice%"=="3" (
    set /p label=Label:
    set /p keywords=Keywords (comma-separated, optional):
    set /p exclude_keywords=Exclude keywords (comma-separated, optional):
    set /p locations=Locations (comma-separated, optional):
    set /p min_salary=Minimum salary (optional):
    set /p ai_prompt=AI prompt (optional):
    set "args=!label!"
    if not "!keywords!"=="" set "args=!args! --keywords "!keywords!""
    if not "!exclude_keywords!"=="" set "args=!args! --exclude-keywords "!exclude_keywords!""
    if not "!locations!"=="" set "args=!args! --locations "!locations!""
    if not "!min_salary!"=="" set "args=!args! --min-salary !min_salary!"
    if not "!ai_prompt!"=="" set "args=!args! --ai-prompt "!ai_prompt!""
    python cli.py add-criteria !args!
) else if "%choice%"=="4" (
    python cli.py criteria
) else if "%choice%"=="5" (
    set /p url=Careers page URL:
    python cli.py discover "!url!"
) else if "%choice%"=="6" (
    set /p url=Careers page URL:
    set /p name=Name (optional):
    if "!name!"=="" (
        python cli.py add-source "!url!"
    ) else (
        python cli.py add-source "!url!" --name "!name!"
    )
) else if "%choice%"=="7" (
    python cli.py sources
) else if "%choice%"=="8" (
    set /p source_id=Source id:
    python cli.py scrape !source_id!
) else if "%choice%"=="9" (
    python cli.py normalize
) else if "%choice%"=="10" (
    set /p criteria_id=Criteria id:
    python cli.py match !criteria_id!
) else if "%choice%"=="11" (
    set /p criteria_id=Criteria id:
    python cli.py ai-match !criteria_id!
) else if /i "%choice%"=="q" (
    exit /b 0
) else (
    echo Unknown option.
)

endlocal
