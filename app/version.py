"""Version of the site. Raise VERSION and add a line at the top of CHANGES with every update."""
VERSION = "1.4.1"
DATE = "19-11-2026"
CHANGES = [
    ("1.4.1", "19-11-2026", [
        "Uitslagen plakken: een teamnaam wordt alleen nog als hele woorden herkend (korte namen als A.I.). "
        "\"Glamour&Pain - A.I.\" werd gelezen als A.I. tegen A.I.; die wedstrijd staat nu goed.",
    ]),
    ("1.4", "19-11-2026", [
        "Nieuw tabblad Uitslagen: per ronde de wedstrijd van elk team (uitslag, wedstrijdpunten, gemiddelde voor en na), "
        "met uitleg waarom een gemiddelde omlaag kan gaan.",
        "Stand: bij een speler staat onder elk van zijn teams nu de uitslag van die ronde en wat die opleverde.",
        "Stand: uitgelegd dat het kleine getal onder een score het verschil met de vorige ronde is.",
    ]),
    ("1.3", "19-11-2026", [
        "Typfout hersteld: Robbie's heet nu Ribbie's.",
        "Organisatie → Spelers: de naam van een speler is nu direct aan te passen (naamveld + Opslaan), "
        "zonder de zes teams te raken. Dubbele namen worden geweigerd.",
    ]),
    ("1.2", "18-11-2026", [
        "Forumtabellen: is een tabel langer dan één forumbericht toestaat, dan wordt hij verdeeld over meerdere "
        "berichten (deel 1/2, 2/2 ...), elk met eigen kop en kopieerknop. Tabellen worden alleen tussen rijen geknipt.",
        "Onder elk blok staat het aantal tekens; de limiet (standaard 3.000) stel je in bovenaan Forumtabellen.",
    ]),
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
