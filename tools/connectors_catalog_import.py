"""Import a pasted connector directory into the connector catalog.

    py -3.13 tools/connectors_catalog_import.py <raw-directory.md> [--dry-run]

Input is the plain text of a connector directory, copied from a web page: each entry is
``[<Name> icon]`` / ``<Name>`` / ``[badge]`` / ``<description>`` / ``by <Vendor>``.

Output is ``navig/connectors/catalog/data/<category>.jsonl``. The import is APPEND-ONLY:
an id already in the catalog is left exactly as it is, so a hand-verified row is never
clobbered by a re-import. New entries are ranked after every existing one.

Descriptions are read only to guess a category, and are never written: the catalog
stores facts (name, vendor, category, country), not the directory's copy.

Every guess this tool makes (category, country) is best-effort and the row stays
``status: planned`` until a person verifies it during its batch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE))

from navig.connectors.catalog import (  # noqa: E402
    BATCH_SIZE,
    CATEGORY_IDS,
    REGIONS,
    CatalogEntry,
    batch_of,
    entry_from_dict,
    parse_rows,
)

DATA = CORE / "navig" / "connectors" / "catalog" / "data"

BADGES = {
    "Beta": "beta",
    "New": "new",
    "Trending": "trending",
    "Community": None,
    "Desktop extension": None,
}

# ── Raw parsing ──────────────────────────────────────────────────────────────


def parse_directory(text: str) -> list[dict]:
    """Split the pasted directory into ``{name, vendor, desc, badges}`` records."""
    out: list[dict] = []
    buf: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("by ") and buf:
            chunk = [x for x in buf if x]
            name, badges, desc = None, [], []
            for x in chunk:
                if name is None:
                    if x.endswith(" icon"):
                        continue
                    name = x
                elif x in BADGES:
                    badges.append(x)
                elif x != f"{name} icon":
                    desc.append(x)
            if name:
                out.append(
                    {
                        "name": name,
                        "vendor": s[3:].strip(),
                        "desc": " ".join(desc),
                        "badges": badges,
                    }
                )
            buf = []
        else:
            buf.append(s)
    return out


# The copy-paste turned some dashes into U+FFFD; and a directory written for one assistant
# names entries after it. Neither belongs in our catalog.
_NAME_SUFFIX = re.compile(r"\s+(?:for Claude|in Claude|MCP Server|MCP)$", re.IGNORECASE)


_TAGLINE = re.compile(r"\s+[–—-]\s+.*$")


def clean_name(name: str) -> str:
    name = name.replace("�", "–").strip()
    # "Fathom – Your Meeting Intelligence Layer" → "Fathom": the tail is a slogan.
    name = _TAGLINE.sub("", name).strip() or name
    prev = None
    while prev != name:
        prev, name = name, _NAME_SUFFIX.sub("", name).strip()
    return name or prev


# The directory credits a few entries to the assistant vendor that hosts them; the
# catalog credits the organisation that owns the service or data.
VENDOR_BY_NAME = {
    "Microsoft 365": "Microsoft",
    "PubMed": "U.S. National Library of Medicine",
    "Clinical Trials": "U.S. National Library of Medicine",
    "bioRxiv": "Cold Spring Harbor Laboratory",
    "ChEMBL": "EMBL-EBI",
    "NPI Registry": "U.S. Centers for Medicare & Medicaid Services",
    "CMS Coverage": "U.S. Centers for Medicare & Medicaid Services",
    "ICD-10 Codes": "U.S. Centers for Medicare & Medicaid Services",
    "Filesystem": "Local",
    "Control Chrome": "Local",
    "pdf-viewer": "Local",
}


def clean_vendor(name: str, vendor: str) -> str:
    vendor = VENDOR_BY_NAME.get(name, vendor).replace("�", "–").strip()
    if vendor == "Model Context Protocol Community":
        return "MCP community"
    return vendor


# ── Category ─────────────────────────────────────────────────────────────────
# (category, name pattern, description pattern). Name rules are tried for every category
# first, then description rules — a name is a far stronger signal than a sentence.
RULES: list[tuple[str, str, str]] = [
    (
        "security",
        r"\b(vanta|drata|sprinto|malwarebytes|norton|mcafee|have i been pwned|1password|okta|crowdstrike|snyk|wiz|tines|sumsub|datagrail|phished)\b",
        r"\b(security|vulnerab\w*|compliance|soc ?2|threat|scams?|breach\w*|pentest|malware|phishing|siem|zero trust|cve|fraud)\b",
    ),
    (
        "legal",
        r"\b(legal|law|court\w*|counsel|harvey|docusign|ironclad|juro|signnow|pandadoc|docuseal|lumin|everlaw|netdocuments|imanage|lawvu|brightflag|clio|trellis|paxton|trademarks?|jus ai)\b",
        r"\b(legal|law firm|lawyers?|attorney\w*|case law|contracts?|e-?signature\w*|litigat\w*|patents?|statutes?|legislation|trademarks?|arbitration)\b",
    ),
    (
        "health",
        r"\b(pubmed|clinical|npi|icd-10|cms coverage|snomed|chembl|biorxiv|medidata|biorender|owkin|boltz|helix|synapse\.org|open targets|eden|inductive bio|health\w*|pharma\w*|bio|genom\w*|alma|medic\w*|elfcare)\b",
        r"\b(clinical|biomedical|patients?|drugs?|pharma\w*|genom\w*|proteins?|molecul\w*|genes?|biolog\w*|health ?care|medical|nutrition|antibod\w*|vaccines?|diseases?|ehr|fhir|hospitals?|therap\w*)\b",
    ),
    (
        "research",
        r"\b(arxiv|alphaxiv|consensus|elicit|scite|scispace|wiley|wolfram|semantic scholar|research)\b",
        r"\b(scientific|scholar\w*|papers?|preprints?|citations?|academic|literature|journals?)\b",
    ),
    (
        "travel",
        r"\b(booking\.com|expedia|kiwi\.com|lastminute\.com|tripadvisor|trivago|airbnb|uber|rome2rio|viator|civitatis|turkish airlines|otto travel|directbooker|super\.com|wyndham\w*|slicktrip|govola|tineo|travexp|novasol|cottages|airfield)\b",
        r"\b(flights?|hotels?|travel|trips?|itinerar\w*|airlines?|airports?|vacations?|holiday homes?|tours?|rides?)\b",
    ),
    (
        "food",
        r"\b(uber eats|instacart|resy|zomato|glovo|carrefour|order by cash app|doordash|opentable)\b",
        r"\b(restaurants?|food|grocer\w*|recipes?|meals?|dining)\b",
    ),
    (
        "payments",
        r"\b(stripe|paypal|square|razorpay|gocardless|airwallex\w*|adyen|plaid\w*|chargebee|wise|mollie|paddle|picpay|privacy\.com)\b",
        r"\b(payments?|invoic\w*|billing|checkout|payouts?|virtual cards?)\b",
    ),
    (
        "crypto",
        r"\b(crypto\w*|coin\w*|blockscout|quicknode|lunarcrush|gemini|lukka|bitcoin|ethereum|solana|web3|defi|\w*chain)\b",
        r"\b(crypto\w*|blockchain\w*|on-?chain|tokens?|wallets?|defi|nfts?|bitcoin|ethereum|solana)\b",
    ),
    (
        "markets",
        r"\b(ibkr|interactive brokers|webull|morningstar\w*|alpha vantage|fmp|twelve data|lseg|factset\w*|s&p\w*|msci\w*|moody's\w*|pitchbook\w*|daloopa|quartr|fiscal\.ai|financial datasets|bigdata\.com|aiera|canary data|blackrock\w*|vanguard\w*|ice data\w*|mt newswires|pinegap|cb insights|crunchbase\w*|third bridge|guidepoint|glg|carta|angellist|affinity|chronograph|kpler|energy aspects|grasp|clarity ai|wealthbox|ibisworld)\b",
        r"\b(stocks?|equit\w*|trading|investments?|investors?|portfolios?|market data|earnings|sec filings|hedge|private equity|venture|asset manag\w*|fixed income|forex|brokerage|commodit\w*)\b",
    ),
    (
        "finance",
        r"\b(quickbooks|xero|netsuite|ramp\w*|brex|mercury|qonto|expensify|gusto|zoho books|freee|digits|rillet|pigment|aleph|datarails\w*|tabs|turbotax|credit karma|pocketsmith\w*|era context|memo bank|tropic|freshbooks)\b",
        r"\b(accounting|bookkeep\w*|finances?|financial|expens\w*|payroll|tax\w*|budget\w*|ledger|fp&a|spend|bank\w*|treasury|erp|underwriting)\b",
    ),
    (
        "hr",
        r"\b(indeed|ziprecruiter|dice|upwork|ashby|workable|teamtailor|lattice|deel|remote|gem|brighthire|metaview|snagajob|oorwin|greenhouse|lever|personio|rippling|bamboohr|shapes|windmill hr|isolved\w*|visier|cloverleaf)\b",
        r"\b(recruit\w*|hiring|candidates?|jobs?|talent|employees?|hr|people data|workforce|performance reviews?)\b",
    ),
    (
        "education",
        r"\b(coursera|udemy\w*|o.reilly|learning commons|brisk\w*|magicschool\w*|coteach|kuliko\w*|padlet|articulate|duolingo|khan|kahoot!?|datacamp|eedi)\b",
        r"\b(teach\w*|students?|k-12|classroom|courses?|learning|education\w*|tutor\w*|curricul\w*|lessons?)\b",
    ),
    (
        "meetings",
        r"\b(zoom|granola|fireflies|otter\.ai|fathom\w*|read ai|tldv|tactiq|fellow\.ai|krisp|grain|circleback|spinach ai|webex\w*|plaud|sybill|attention|gong|chorus|avoma|calendly|reclaim\.ai|google calendar|calendar|livestorm)\b",
        r"\b(meetings?|transcripts?|calendars?|schedul\w*|call recordings?|notetaker|webinars?)\b",
    ),
    (
        "email-marketing",
        r"\b(mailchimp|mailerlite|kit|brevo|klaviyo|loops|customer\.io|omnisend\w*|activecampaign|beehiiv|braze)\b",
        r"\b(email marketing|newsletters?|email campaigns?)\b",
    ),
    (
        "email",
        r"\b(gmail|superhuman mail|fastmail|outlook|fyxer|agentmail|inkbox|resend|lz virtual mail)\b",
        r"\b(emails?|inbox\w*)\b",
    ),
    (
        "support",
        r"\b(intercom|zendesk|freshdesk|freshservice|zoho desk|pylon|servicenow|gainsight\w*|helpscout|devrev|lorikeet)\b",
        r"\b(support tickets?|customer support|help ?desk|itsm|service desk|customer success)\b",
    ),
    (
        "communication",
        r"\b(slack|teams|discord|telegram|whatsapp|twilio|ringcentral\w*|quo|letsbot)\b",
        r"\b(messages?|messaging|chat|sms|voice calls?|phone|text messages)\b",
    ),
    (
        "sales",
        r"\b(hubspot|salesforce|pipedrive|apollo\.io|zoominfo|clay|lusha|rocketreach|attio|close|zoho crm|bigin\w*|outreach|salesloft|nooks|seamless|day ai|clarify|lightfield|common room|crossbeam|euler|highspot|seismic|klue|vibe prospecting|harmonic|crustdata|sumble|phoenix\w*|g2|sprouts\w*|cloze|valuecase\w*|zocks|productised)\b",
        r"\b(crm|sales|prospect\w*|leads?|pipelines?|deals?|outreach|b2b|go-to-market|gtm|enrich\w*)\b",
    ),
    (
        "seo",
        r"\b(semrush|ahrefs|ubersuggest|similarweb|profound|peec ai|conductor|airops|local falcon|openrush|openseo|search console)\b",
        r"\b(seo|keywords?|backlinks?|serp|search visibility|ai search|rank tracking)\b",
    ),
    (
        "marketing",
        r"\b(windsor\.ai|supermetrics\w*|adobe marketing agent|adobe journey optimizer|motion creative analytics|adwhispr\w*|adspirer\w*|helena\w*|tiktok for business|funnel|jasper|bitly|metricool\w*|typefully\w*|meltwater|brand24|brandfetch|frontify|insider one|onesignal|abency|adobe customer journey analytics)\b",
        r"\b(ads?|advertis\w*|campaigns?|marketing|brands?|social media|influencers?|ppc|attribution|audiences?)\b",
    ),
    (
        "commerce",
        r"\b(shopify|amazon\w*|helium 10|aftership\w*|shippo|etsy|woocommerce|ebay|stubhub|ticketmaster|ticket tailor|cargurus|thumbtack|taskrabbit\w*|aws marketplace|flexport|terminal49|cargoai|bling|jobber|givebutter|shiplab)\b",
        r"\b(e-?commerce|stores?|shop\w*|products?|orders?|shipping|shipments?|inventory|merchants?|marketplace|tickets?|retail|fundrais\w*)\b",
    ),
    (
        "observability",
        r"\b(sentry|datadog|honeycomb|coralogix|pagerduty|incident\.io|grafana|new relic|monte carlo|subtext|fullstory|jam)\b",
        r"\b(observability|logs?|traces?|telemetry|incidents?|on-?call|monitor\w*|errors?|apm|debug\w*)\b",
    ),
    (
        "cloud",
        r"\b(aws\w*|azure\w*|google cloud\w*|google compute engine|cloudflare\w*|vercel|netlify|railway|render|heroku|fly\.io|digitalocean|expo|replit|lovable|base44|floot|macaly\w*|v0|wix|webflow|wordpress\.com|godaddy|weweb|website generator\w*|harness|alpic|sprites|docfarm|send|port io)\b",
        r"\b(deploy\w*|hosting|infrastructure|cloud|serverless|kubernetes|websites?|web apps?|domains?)\b",
    ),
    (
        "data",
        r"\b(snowflake|bigquery|clickhouse|motherduck|databricks\w*|supabase|neon|planetscale|mongodb\w*|postgres\w*|mysql|airtable|smartsheet|hex|sigma|metabase|omni analytics|thoughtspot\w*|datahub|cdata\w*|coupler\.io|polar analytics|unstructured\w*|llamaparse|google sheets|excel|duckdb|redis|autosheet|starburst|tableau|microsoft dataverse|zite|atlasfindings|mulesoft)\b",
        r"\b(databases?|sql|warehouse|data ?lakes?|spreadsheets?|etl|dashboards?|bi)\b",
    ),
    (
        "analytics",
        r"\b(amplitude|mixpanel|posthog|pendo|contentsquare|google analytics|ga4|heap|chartmogul|fullstory|august|orion by gravity)\b",
        r"\b(analytics|metrics|kpis?|funnels?|retention)\b",
    ),
    (
        "developer",
        r"\b(github|gitlab|bitbucket|postman|swagger|context7|sourcegraph|stack overflow|microsoft learn|mintlify|hugging face|circleci|clerk|workos|stytch|auth0|graphos\w*|unblocked|runway \(runway\.team\)|roboflow|youtrack|ketryx|tseha\.io|webmcp evaluator|autodesk product help|playmcp)\b",
        r"\b(code\w*|developers?|apis?|sdks?|repositor\w*|pull requests?|ci/cd|git|programming|devops|mcp servers?)\b",
    ),
    (
        "web",
        r"\b(exa|firecrawl|tavily|nimble|tinyfish|parallel search|perplexity|brave\w*|serpapi|apify|browserbase|bright data|control chrome)\b",
        r"\b(web search|scrap\w*|crawl\w*|browser automation|search the web|web data)\b",
    ),
    (
        "automation",
        r"\b(zapier|make|n8n|ifttt|workato|tray\.ai|codewords|process street|pipedream|windows)\b",
        r"\b(automat\w*|workflows?|no-?code)\b",
    ),
    (
        "design",
        r"\b(figma|canva|adobe|adobe experience manager|miro|lucid|excalidraw|whimsical|tldraw|eraser|mermaid chart|trimble sketchup|mobbin|magic patterns|flourish|felt maps|three\.js 3d viewer|shutterstock|unsplash|cloudinary|moda\w*|gamma|pitch|slidesgpt|brightdeck|hyperframes\w*|google slides|dot\.)\b",
        r"\b(design\w*|diagrams?|whiteboards?|figures?|images?|illustrat\w*|presentations?|slides?|mockups?|wireframes?|3d|charts?|visual\w*)\b",
    ),
    (
        "media",
        r"\b(spotify|audible|sonos|melon|splice|viberate\w*|descript|opusclip|riverside|tella|elevenlabs|vidiq|youtube|heygen|speko|play sheet music|kick)\b",
        r"\b(videos?|audio|music|podcasts?|voice|songs?|clips?|audiobooks?|sounds?|streaming|speech|news)\b",
    ),
    (
        "files",
        r"\b(google drive|dropbox|box|onedrive|egnyte|files\.com|filesystem|sharepoint|pdf\w*|microsoft 365)\b",
        r"\b(files?|storage|folders?|pdfs?)\b",
    ),
    (
        "knowledge",
        r"\b(notion|confluence|google docs|craft|roam research|mem|mem0|readwise|guru|glean|goodnotes|obsidian|evernote|superhuman docs|dovetail|enterpret|unwrap|listen labs|within|sanity|contentful|meko|campfire|ontra|datasite)\b",
        r"\b(notes?|knowledge|wiki|docs|documents?|memory|second brain|bookmarks?|highlights?|content)\b",
    ),
    (
        "projects",
        r"\b(asana|linear|monday\.com|clickup|trello|todoist|ticktick|jira|atlassian\w*|wrike|adobe workfront|productive|fibery|basecamp|shortcut)\b",
        r"\b(tasks?|projects?|issues?|to-?dos?|boards?|kanban|sprints?|roadmaps?)\b",
    ),
    (
        "forms",
        r"\b(jotform\w*|tally|typeform|surveymonkey|cognito forms|perspective ai|rally)\b",
        r"\b(forms?|surveys?|quiz\w*|questionnaires?)\b",
    ),
    (
        "real-estate",
        r"\b(idealista|yardi\w*|appfolio\w*|zillow|redfin)\b",
        r"\b(real estate|propert\w*|rentals?|landlords?|tenants?|mortgages?)\b",
    ),
    (
        "public-data",
        r"\b(mospi|govtribe|tango|candid|kindora\w*|granted|demographic and health surveys|economic index|pophive|oxford economics|census|planning center|maryland community compass|siren)\b",
        r"\b(government|public data|statistic\w*|nonprofits?|grants?|census|procurement|economic\w*)\b",
    ),
    (
        "lifestyle",
        r"\b(strava|accuweather\w*|vaisala xweather|alltrails|tomtom maps|google maps|fever event discovery|weather|pga|mygeotab)\b",
        r"\b(weather|maps?|routing|geocod\w*|hikes?|fitness|games?|sports?|golf|fleet)\b",
    ),
    (
        "ai",
        r"\b(openai|anthropic|mistral|replicate|together|groq|ollama|langchain|llama\w*|lilt|deepl|smartling|translate)\b",
        r"\b(ai agents?|llms?|machine learning|models?|prompts?|embeddings?|rag|translat\w*)\b",
    ),
]
if not {r[0] for r in RULES} <= CATEGORY_IDS:  # a plain check: asserts vanish under -O
    raise SystemExit(
        f"RULES use unknown categories: {sorted({r[0] for r in RULES} - CATEGORY_IDS)}"
    )
_COMPILED = [(c, re.compile(n), re.compile(d)) for c, n, d in RULES]

# Decisions the rules get wrong — kept by name so a reviewer can read them.
CATEGORY_OVERRIDES = {
    "Google Calendar": "meetings",
    "Calendly": "meetings",
    "Microsoft 365": "files",
    "Atlassian": "projects",
    "Google Sheets": "data",
    "Google Docs": "knowledge",
    "Google Slides": "design",
    "Hugging Face": "ai",
    "Supabase": "data",
    "Zoom": "meetings",
    "Gemini": "crypto",
}


def categorize(name: str, desc: str) -> str:
    if name in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[name]
    n, d = name.lower(), desc.lower()
    for cat, nr, _ in _COMPILED:
        if nr.search(n):
            return cat
    for cat, _, dr in _COMPILED:
        if dr.search(d):
            return cat
    return "other"


# ── Country ──────────────────────────────────────────────────────────────────
# Headquarters of vendors we are confident about. Unknown stays None — a blank is
# honest; a guess dressed as data is not. Fill the rest during each batch review.
VENDOR_COUNTRY: dict[str, str] = {
    **dict.fromkeys(
        [
            "google",
            "microsoft",
            "notion",
            "figma",
            "slack",
            "hubspot",
            "asana",
            "linear (linear orbit, inc.)",
            "intercom",
            "box",
            "adobe",
            "adobe inc.",
            "adobe inc",
            "supabase",
            "indeed",
            "zoom",
            "gamma",
            "vercel",
            "wispr flow",
            "zapier",
            "salesforce",
            "airtable",
            "clickup",
            "strava, inc.",
            "apollo.io",
            "docusign",
            "sentry.io",
            "datadog",
            "stripe",
            "smartsheet inc.",
            "zoominfo technologies inc.",
            "automattic (wordpress.com)",
            "consensus",
            "cloudflare",
            "dropbox",
            "semrush holdings, inc.",
            "postman, inc.",
            "netlify",
            "expedia group",
            "intuit quickbooks",
            "intuit",
            "intuit mailchimp",
            "pitchbook",
            "snowflake",
            "clay",
            "fireflies ai corp",
            "s&p global",
            "webflow",
            "amplitude",
            "superhuman mail",
            "superhuman",
            "interactive brokers",
            "trello",
            "fathom",
            "exa",
            "firecrawl",
            "posthog",
            "jotform",
            "jotform inc.",
            "calendly",
            "descript",
            "factset research systems",
            "klaviyo",
            "ramp",
            "mixpanel",
            "uber",
            "tripadvisor",
            "ziprecruiter inc.",
            "harvey",
            "replit",
            "ironclad, inc",
            "pendo",
            "alpha vantage inc",
            "paypal",
            "morningstar",
            "scite",
            "elicit, inc.",
            "financial modeling prep",
            "free law project",
            "read ai",
            "glean",
            "surveymonkey",
            "otter.ai",
            "carta",
            "resend",
            "outreach",
            "dice.com",
            "coursera",
            "ashby",
            "workato",
            "oracle netsuite",
            "resy",
            "udemy, inc.",
            "wrike",
            "accuweather inc.",
            "pagerduty",
            "alltrails",
            "affinity",
            "hex",
            "aws marketplace",
            "amazon web services",
            "amazon",
            "sigma computing",
            "whimsical",
            "railway",
            "twilio",
            "audible",
            "g2",
            "cb insights",
            "lattice",
            "brex",
            "omni analytics",
            "guru",
            "autodesk, inc.",
            "profound",
            "square, inc.",
            "pandadoc",
            "gusto",
            "cloudinary",
            "render",
            "fullstory",
            "clickhouse",
            "coindesk",
            "o’reilly media",
            "pylon",
            "jam",
            "ifttt, inc.",
            "cdata",
            "lunarcrush",
            "mercury",
            "wolfram research",
            "highspot",
            "upwork",
            "cisco systems, inc",
            "close",
            "common room",
            "monte carlo",
            "braze inc",
            "midpage",
            "malwarebytes",
            "mintlify",
            "databricks",
            "planetscale",
            "mongodb",
            "sourcegraph",
            "expensify, inc.",
            "motherduck corporation",
            "servicenow",
            "ticketmaster",
            "stubhub",
            "metabase",
            "honeycomb.io",
            "drata inc.",
            "agentmail, inc.",
            "vanta inc",
            "customer.io",
            "crossbeam",
            "neon (a databricks, inc. company)",
            "shippo",
            "chargebee inc",
            "plaid",
            "clerk",
            "workos",
            "stack overflow",
            "circleci engineering",
            "the l suite",
            "gainsight",
            "freshworks",
            "expo",
            "instacart",
            "thumbtack",
            "taskrabbit",
            "cargurus, inc.",
            "ringcentral, inc.",
            "block, inc.",
            "dun & bradstreet",
            "moody's analytics",
            "msci inc",
            "blackrock",
            "vanguard",
            "intercontinental exchange, inc.",
            "crunchbase",
            "rocketreach",
            "nooks",
            "salesloft",
            "gem",
            "brighthire",
            "deel",
            "remote technology services, inc.",
            "verisk analytics",
            "everlaw",
            "imanage llc",
            "netdocuments",
            "bloomberg industry group",
            "legalzoom",
            "hg insights",
            "harmonic",
            "crustdata",
            "sumble, inc",
            "day ai",
            "clarify",
            "lightfield",
            "seismic",
            "pilot.com, inc.",
            "candid",
            "granted ai",
            "acryl data, inc.",
            "tray.ai",
            "yardi",
            "yardi systems inc",
            "appfolio, inc.",
            "planning center",
            "quicknode",
            "gemini space station, llc",
            "webull",
            "financial datasets, inc.",
            "aiera",
            "guidepoint",
            "gerson lehrman group",
            "daloopa",
            "fiscal.ai",
            "angellist",
            "chronograph",
            "ontra",
            "datasite",
            "kit, inc.",
            "beehiiv",
            "activecampaign",
            "loops",
            "mem labs, inc.",
            "roam research inc.",
            "eraser",
            "magic patterns",
            "lucid software",
            "github",
            "perplexity ai",
            "openai",
            "anthropic",
            "elevenlabs",
            "godaddy",
            "wix.com",
            "u.s. national library of medicine",
            "u.s. centers for medicare & medicaid services",
            "cold spring harbor laboratory",
        ],
        "US",
    ),
    "atlassian": "AU",
    "canva": "AU",
    "dovetail": "AU",
    "fastmail": "AU",
    "rome2rio": "AU",
    "have i been pwned": "AU",
    "xero limited": "NZ",
    "pocketsmith ltd": "NZ",
    "lawvu limited": "NZ",
    "miro": "NL",
    "booking bv": "NL",
    "tomtom": "NL",
    "moonlit": "NL",
    "spotify": "SE",
    "quartr": "SE",
    "teamtailor": "SE",
    "lovable": "SE",
    "kiwi.com": "CZ",
    "make": "CZ",
    "deepl": "DE",
    "n8n gmbh": "DE",
    "trivago": "DE",
    "pitch": "DE",
    "tldx solutions gmbh": "DE",
    "qonto": "FR",
    "contentsquare": "FR",
    "owkin": "FR",
    "brevo": "FR",
    "memo bank": "FR",
    "carrefour": "FR",
    "pigment": "FR",
    "glovo": "ES",
    "idealista s.a.u.": "ES",
    "civitatis": "ES",
    "additiu marketing digital sl": "ES",
    "ravenpack": "ES",
    "lastminute.com": "CH",
    "frontify": "CH",
    "productive": "HR",
    "viberate": "SI",
    "omnisend": "LT",
    "mailerlite": "LT",
    "brand24 global inc.": "PL",
    "craft docs ltd": "HU",
    "vaisala xweather": "FI",
    "lseg": "GB",
    "oxford economics": "GB",
    "third bridge group": "GB",
    "gocardless": "GB",
    "juro": "GB",
    "basecamp research": "GB",
    "fyxer": "GB",
    "tldraw": "GB",
    "granola": "GB",
    "incident.io": "GB",
    "attio": "GB",
    "metaview": "GB",
    "brightflag": "IE",
    "tines": "IE",
    "wix": "IL",
    "monday.com": "IL",
    "similarweb": "IL",
    "lusha": "IL",
    "riverside": "IL",
    "insider one": "TR",
    "turkish airlines": "TR",
    "razorpay software private limited": "IN",
    "eternal limited": "IN",
    "sprinto": "IN",
    "zoho": "IN",
    "zoho corporation": "IN",
    "ministry of statistics and programme implementation (mospi), government of india": "IN",
    "freee k.k.": "JP",
    "kakao": "KR",
    "kakao entertainment": "KR",
    "ahrefs pte. ltd.": "SG",
    "crypto.com": "SG",
    "aftership": "HK",
    "shopify": "CA",
    "unsplash": "CA",
    "readwise inc.": "CA",
    "klue labs": "CA",
    "thomson reuters": "CA",
    "embl-ebi": "GB",
}

# A country named in the entry's own text is a strong hint for vendors not in the table.
_TEXT_COUNTRY = [
    (re.compile(r"[぀-ヿ]"), "JP"),
    (re.compile(r"[가-힯]"), "KR"),
    (re.compile(r"台灣|臺灣|\bTaiwan\b"), "TW"),
    (re.compile(r"\b(Brasil|Brazil|brasileir\w*)\b"), "BR"),
    (re.compile(r"\b(France|SIREN|françaises?|français)\b"), "FR"),
    (re.compile(r"\b(Deutschland|German businesses|in Germany)\b"), "DE"),
    (re.compile(r"\b(España|in Spain)\b"), "ES"),
    (re.compile(r"\b(UK|United Kingdom|British)\b"), "GB"),
    (re.compile(r"\b(India|Indian)\b"), "IN"),
    (re.compile(r"\b(Australia|Australian)\b"), "AU"),
    (re.compile(r"\b(Canada|Canadian)\b"), "CA"),
    (re.compile(r"\b(Nederland|Dutch)\b"), "NL"),
    (re.compile(r"\b(Italia|Italian)\b"), "IT"),
    (re.compile(r"\b(México|Mexico|Mexican)\b"), "MX"),
]


def guess_country(vendor: str, name: str, desc: str) -> str | None:
    c = VENDOR_COUNTRY.get(vendor.lower())
    if c:
        return c
    text = f"{name} {desc}"
    for rx, code in _TEXT_COUNTRY:
        if rx.search(text):
            return code
    return None


if not set(VENDOR_COUNTRY.values()) <= set(REGIONS):
    raise SystemExit(
        f"VENDOR_COUNTRY uses countries with no region: {sorted(set(VENDOR_COUNTRY.values()) - set(REGIONS))}"
    )

# ── Ranking ──────────────────────────────────────────────────────────────────
# The first batches, in order — chosen for an OPERATOR platform: everyday work tools,
# then the infrastructure and data stack NAVIG drives, then revenue and automation.
# Every name must exist (after clean_name) or the import fails: a typo here would
# otherwise silently demote the service it meant to promote.
PRIORITY: list[str] = [
    # 1 — everyday work (the seven native connectors are already live)
    "Gmail",
    "Google Calendar",
    "Google Drive",
    "Notion",
    "Slack",
    "Linear",
    "Supabase",
    "GitHub",
    "Google Docs",
    "Google Sheets",
    "Microsoft 365",
    "Atlassian",
    "Figma",
    "Canva",
    "Dropbox",
    "Box",
    "Zoom",
    "Asana",
    "Todoist",
    "Trello",
    "ClickUp",
    "monday.com",
    "Airtable",
    "HubSpot",
    "Google Slides",
    # 2 — the infrastructure and data stack
    "Cloudflare Developer Platform",
    "Vercel",
    "Netlify",
    "Railway",
    "Render",
    "AWS",
    "Google Cloud BigQuery",
    "Google Compute Engine",
    "Sentry",
    "Datadog",
    "PagerDuty",
    "incident.io",
    "Honeycomb",
    "PostHog",
    "Stripe",
    "Neon",
    "PlanetScale",
    "MongoDB Atlas",
    "Snowflake",
    "ClickHouse",
    "Postman",
    "Context7",
    "Hugging Face",
    "CircleCI",
    "Expo",
    # 3 — automation, web, messaging, revenue
    "Zapier",
    "Make",
    "n8n",
    "IFTTT",
    "Exa",
    "Firecrawl",
    "Tavily",
    "Perplexity",
    "YouTube",
    "Google Maps",
    "Google Cloud Translation",
    "Twilio",
    "Resend",
    "Intercom",
    "Shopify",
    "PayPal",
    "Square",
    "Salesforce",
    "Pipedrive",
    "Calendly",
    "Granola",
    "Fireflies",
    "Otter.ai",
    "Microsoft Learn",
    "Sourcegraph",
    # 4 — creative, content, finance basics
    "Miro",
    "Lucid",
    "Excalidraw",
    "Mermaid Chart",
    "Webflow",
    "WordPress.com",
    "Wix",
    "GoDaddy",
    "Cloudinary",
    "Unsplash",
    "ElevenLabs",
    "Descript",
    "Spotify",
    "Gamma",
    "DeepL",
    "Mixpanel",
    "Amplitude",
    "Metabase",
    "Smartsheet",
    "Jotform",
    "Tally",
    "Xero",
    "Intuit QuickBooks",
    "Mercury",
    "Ramp",
]

# Official entries in these categories are specialist verticals: they rank after the
# general-purpose official entries (still before desktop and community).
NICHE = {
    "health",
    "legal",
    "markets",
    "research",
    "public-data",
    "real-estate",
    "education",
    "crypto",
}

# Connectors NAVIG already ships that the pasted directory does not list.
NAVIG_ONLY = [
    {
        "name": "GitHub",
        "vendor": "GitHub",
        "native": "github",
        "category": "developer",
        "country": "US",
    },
    {
        "name": "YouTube",
        "vendor": "Google",
        "native": "youtube",
        "category": "media",
        "country": "US",
    },
    {
        "name": "Google Maps",
        "vendor": "Google",
        "native": "google_maps",
        "category": "lifestyle",
        "country": "US",
    },
    {
        "name": "Perplexity",
        "vendor": "Perplexity AI",
        "native": "perplexity",
        "category": "web",
        "country": "US",
    },
    {
        "name": "Google Cloud Translation",
        "vendor": "Google",
        "native": "gcp_translate",
        "category": "ai",
        "country": "US",
    },
]
# Directory names that ARE one of our native connectors.
NATIVE_BY_NAME = {
    "Gmail": "gmail",
    "Google Calendar": "google_calendar",
    "Google Drive": "google_drive",
    "Notion": "notion",
    "Slack": "slack",
    "Linear": "linear",
    "Supabase": "supabase",
}


def slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s


def build(records: list[dict], existing: list[CatalogEntry]) -> list[CatalogEntry]:
    """Turn raw records into NEW entries, ranked after ``existing``. Existing ids are kept."""
    have_ids = {e.id for e in existing}
    have_keys = {(e.name.lower(), e.vendor.lower()) for e in existing}
    cands: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for r in NAVIG_ONLY:
        cands.append({**r, "tier": "navig", "flags": []})
    for r in records:
        name = clean_name(r["name"])
        vendor = clean_vendor(name, r["vendor"])
        tier = (
            "community"
            if "Community" in r["badges"]
            else "desktop"
            if "Desktop extension" in r["badges"]
            else "official"
        )
        cands.append(
            {
                "name": name,
                "vendor": vendor,
                "tier": tier,
                "category": categorize(name, r["desc"]),
                "country": guess_country(vendor, name, r["desc"]),
                "native": NATIVE_BY_NAME.get(name) if tier == "official" else None,
                "flags": sorted({BADGES[b] for b in r["badges"] if BADGES.get(b)}),
            }
        )

    unique: list[dict] = []
    for c in cands:
        key = (c["name"].lower(), c["vendor"].lower())
        if key in seen or key in have_keys:
            continue
        seen.add(key)
        unique.append(c)

    # Order: PRIORITY, then official general, official niche, navig leftovers, desktop, community.
    by_name: dict[str, dict] = {}
    for c in unique:
        if c["tier"] in ("navig", "official"):
            by_name.setdefault(c["name"], c)
    missing = [
        n
        for n in PRIORITY
        if n not in by_name and n.lower() not in {e.name.lower() for e in existing}
    ]
    if missing:
        raise SystemExit(f"PRIORITY names not found in the directory: {missing}")
    picked = [by_name[n] for n in PRIORITY if n in by_name]
    picked_ids = {id(c) for c in picked}
    rest = [c for c in unique if id(c) not in picked_ids]
    tier_order = {"official": 0, "navig": 2, "desktop": 3, "community": 4}

    def bucket(c: dict) -> int:
        t = tier_order[c["tier"]]
        return t + 1 if c["tier"] == "official" and c["category"] in NICHE else t

    ordered = picked + sorted(rest, key=bucket)  # sorted() is stable: directory order survives

    out: list[CatalogEntry] = []
    rank = max((e.rank for e in existing), default=0)
    taken = set(have_ids)
    for c in ordered:
        rank += 1
        base = slugify(c["name"]) or slugify(c["vendor"]) or "entry"
        cid = base
        if cid in taken:
            cid = f"{base}-{slugify(c['vendor'])}".strip("-")
        n = 2
        while cid in taken:
            cid = f"{base}-{n}"
            n += 1
        taken.add(cid)
        native = c.get("native")
        out.append(
            CatalogEntry(
                id=cid,
                name=c["name"],
                vendor=c["vendor"],
                category=c["category"],
                country=c.get("country"),
                tier=c["tier"],
                rank=rank,
                batch=batch_of(rank),
                status="live" if native else "planned",
                integration="native" if native else None,
                native=native,
                mcp=None,
                flags=tuple(c.get("flags") or ()),
            )
        )
    return out


def load_existing() -> list[CatalogEntry]:
    if not DATA.is_dir():
        return []
    files = sorted((p.name, p.read_text(encoding="utf-8")) for p in DATA.glob("*.jsonl"))
    return parse_rows(files)


def write(entries: list[CatalogEntry]) -> dict[str, int]:
    DATA.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[CatalogEntry]] = {}
    for e in sorted(entries, key=lambda e: e.rank):
        groups.setdefault(e.category, []).append(e)
    for stale in DATA.glob("*.jsonl"):
        if stale.stem not in groups:
            stale.unlink()
    for cat, rows in groups.items():
        (DATA / f"{cat}.jsonl").write_text(
            "".join(r.to_row() + "\n" for r in rows), encoding="utf-8", newline="\n"
        )
    return {k: len(v) for k, v in groups.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("raw", type=Path, help="the pasted directory text")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    records = parse_directory(args.raw.read_text(encoding="utf-8", errors="replace"))
    existing = load_existing()
    new = build(records, existing)
    allrows = existing + new
    for e in new:  # validate each new row exactly as the loader will
        entry_from_dict(json.loads(e.to_row()), where=e.id)

    print(f"parsed {len(records)} directory entries · existing {len(existing)} · new {len(new)}")
    print(
        f"{batch_of(max(e.rank for e in allrows))} batches of {BATCH_SIZE}"
        if allrows
        else "catalog is empty"
    )
    if args.dry_run:
        return 0
    counts = write(allrows)
    print(f"wrote {sum(counts.values())} rows across {len(counts)} category files → {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
