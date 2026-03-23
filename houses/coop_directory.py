"""Reference coop house directory transcribed from the PDF grand-livre table.

Source document:
    infos_coop/446-chce-liste-numeros-compte-depenses-grand-livre-1.pdf
"""

from collections import OrderedDict


PDF_SOURCE_PATH = "infos_coop/446-chce-liste-numeros-compte-depenses-grand-livre-1.pdf"


RAW_COOP_HOUSE_ROWS = [
    ("AA", "11", "512-514, rue Short", "Le Nid"),
    ("B", "12", "627 à 633, rue de London", "La Britannique"),
    ("BA", "61", "506-508, rue Laurier", "La Centenaire"),
    ("BB", "13", "1215, rue Kitchener", "La Pédagogique"),
    ("C", "14", "902-910, Jacques-Cartier nord", "Du Huard"),
    ("CA", "62", "1427, rue de Courville", "Maison du Rocher"),
    ("CB", "63", "1429, rue de Courville", "Cap-Vent"),
    ("CC", "64", "1431, rue de Courville", "Cap-Boisé"),
    ("CD", "65", "1433, rue de Courville", "Maison du Sentier"),
    ("CE", "66", "1435, rue de Courville", "Maison des Falaises"),
    ("D", "69", "515-533 rue Chalifoux / 431 6e ave.", "Citoyens Unis"),
    ("DA", "15", "111 à 121, rue Magog", "Les Gorges de la Magog"),
    ("DB", "16", "289, rue Belvédère Nord", "La Vigne du Nord"),
    ("DC", "17", "286-290, rue Moore", "G.H. Bradford"),
    ("DD", "18", "254-272,rue Montréal et 360 William", "Du Vieux-Sherbrooke"),
    ("E", "19", "508-514, rue de London", "La Londonnienne"),
    ("EA", "20", "412-422, rue des Fusiliers", "Le Bonheur des Nations"),
    ("EB", "21", "432, rue des Fusiliers", "Les Jardins d'Hortense"),
    ("F", "22", "658, rue Prospect", "D'Egell-Wash"),
    ("FA", "23", "762, rue Argyll", "Des Cèdres"),
    ("G", "70", "206-214, rue Salaberry", "Le Colibri"),
    ("GA", "24", "189-191, rue Brooks", "Du Tilleul"),
    ("H", "25", "412-418, rue Laurier", "La Centrevilloise"),
    ("HA", "26", "366-370, rue Laurier", "Des Érables"),
    ("HB", "27", "161-163, rue Ball", "La Tourelle"),
    ("HC", "28", "390, rue Brooks", "La Haute Campagne"),
    ("HD", "29", "401-403, rue Laurier", "Des Remparts"),
    ("HE", "30", "378-384, rue Brooks", "Les Grands Espaces"),
    ("IA", "31", "858-868, rue St-Pierre", "La Luciole"),
    ("IB", "32", "989, rue Princesse", "La Chambranle"),
    ("IC", "33", "963-967, rue Larocque", "Le Soleil Levant"),
    ("J", "34", "735-745, rue Ontario", "Les Jumelles du Vieux-Nord"),
    ("JA", "35", "213-219, rue Laurier", "Le Château Laurier"),
    ("JB", "36", "227-233, rue Laurier", "Le Pavillon Laurier"),
    ("K", "37", "1263-1269, rue Prospect", "La Wilson"),
    ("L", "38", "433-445, rue de Vimy", "La Closerie des Cèdres"),
    ("L", "38", "459-461, rue de Vimy", "La Closerie des Cèdres"),
    ("LA", "39", "815, rue Malouin", "Mur-Mur"),
    ("LB", "40", "824, rue Malouin", "Alice-Gravel"),
    ("MB", "41", "336, rue Moore", "La Dauphine"),
    ("NA", "42", "970, rue Fabre", "Le Petit-Canada"),
    ("NC", "43", "1001-1003, Fabre/ 268 Guérin-Lajoie", "Du Clocher"),
    ("OA", "71", "1411, rue Cousineau", "La Belle Cousineau"),
    ("OB", "72", "1395, rue des Sables", "La Maison des Sables"),
    ("OD", "74", "1441, rue Cousineau", "Nom à venir"),
    ("P", "44", "1071, rue Prospect", "La Marmottière"),
    ("QA", "45", "1725, rue Dunant", "L'Orée de Bellevue"),
    ("QB", "46", "1741-1743, rue Dunant", "L'Orée de Bellevue"),
    ("R", "47", "716, rue King George", "Des Boiseries"),
    ("RA", "48", "950, rue Perry et 750 King Georges", "Les Deux Tours"),
    ("S", "49", "29-31, rue Bruno-Dandenault", "L'Unique"),
    ("T", "50", "395-405, rue St-Michel", "Le St-Michel"),
    ("U", "51", "206, rue Laurier", "Le Laurier Rose"),
    ("UA", "52", "189-193, rue Gillespie", "La Maison Racine"),
    ("UB", "67", "228-232, rue Gillespie", "Nom à venir"),
    ("V", "68", "95 rue Victoria", "Ste-Thérèse"),
    ("VA", "53", "1150-1154, rue Champlain", "Le P'tit Champlain"),
    ("VB", "54", "905, rue Veilleux", "Vega"),
    ("W", "55", "982-988, rue Princesse", "La Loggia-épicière"),
    ("W", "55", "996, rue Princesse", "La Loggia-épicière"),
    ("WA", "56", "1590, rue Dunant", "La Charpente"),
    ("WB", "57", "88-90, rue Rioux", "Ma Campagne"),
    ("WB", "57", "92-96, rue Rioux", "Ma Campagne"),
    ("X", "58", "1165, rue Fabre", "La Salamandre"),
    ("Y", "59", "240-246, rue Laurier", "La Sitelle"),
    ("Z", "60", "157, rue Cate", "La Maison Marengo"),
]


def coop_house_directory():
    """Return merged house records keyed by house code."""
    merged = OrderedDict()

    for code, accounting_code, address, name in RAW_COOP_HOUSE_ROWS:
        record = merged.setdefault(
            code,
            {
                "code": code,
                "accounting_code": accounting_code,
                "name": name,
                "addresses": [],
            },
        )
        if record["accounting_code"] != accounting_code:
            raise ValueError(
                f"Le code comptable dupliqué pour {code} ne concorde pas: "
                f"{record['accounting_code']} vs {accounting_code}."
            )
        if record["name"] != name:
            raise ValueError(
                f"Le nom de maison dupliqué pour {code} ne concorde pas: "
                f"{record['name']} vs {name}."
            )
        if address not in record["addresses"]:
            record["addresses"].append(address)

    return [
        {
            "code": record["code"],
            "accounting_code": record["accounting_code"],
            "name": record["name"],
            "account_number": f"{record['accounting_code']}-51200",
            "address": "\n".join(record["addresses"]),
        }
        for record in merged.values()
    ]


COOP_HOUSE_DIRECTORY = tuple(coop_house_directory())
