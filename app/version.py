"""Version of the site. Raise VERSION and add a line at the top of CHANGES with every update."""
VERSION = "1.1"
DATE = "15-11-2026"
CHANGES = [
    ("1.1", "15-11-2026", [
        "Uitslagen plakken: regels uit Hattricks wedstrijdlijst met nummer, datum en tijd ervoor worden nu gelezen "
        "(bv. \"1 11-11-2026 17:00 Buurtteam - The Fighting Hyenas 0 - 13\"). Een gelijke stand zonder shoot-out "
        "wordt op strafschoppen gezet: vul de shoot-out nog in.",
        "Teams: kies de volgorde. Standaard verst in de beker eerst (nog in de beker, dan hoe later uitgeschakeld), "
        "daarna het gemiddelde.",
        "Gelijke scores delen een plaats en staan op alfabet (spelers en teams).",
        "Het versienummer staat onderaan elke pagina en bij /healthz.",
    ]),
    ("1.0", "13-11-2026", ["Eerste versie op Render."]),
]
