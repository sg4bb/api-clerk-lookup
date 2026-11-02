"""Arresto en otro condado con una orden de este (02/11/2026): Peters, "Clavicular".

La policía de Fort Lauderdale (Broward) lo detuvo con una orden de Osceola. El caso es
del Sheriff de Osceola: se busca en Osceola y la agencia que lo detuvo no cuenta.
"""

import unittest
from datetime import date

from core.charges import categorize
from core.matcher import pick_best, score_case
from core.models import CaseCandidate, CaseDetails, Charge
from extractor import build_query, warrant_county
from extractor.fetch import Article
from extractor.schema import Extraction

PETERS_TEXT = ("Social media influencer \"Clavicular,\" whose real name is Braden Peters, was arrested in Broward "
               "County on a warrant out of Osceola County. Peters is wanted for misdemeanor battery and criminal "
               "conspiracy to commit battery. He was arrested on Thursday, March 26, by the Fort Lauderdale Police "
               "Department related to an out of county hold.")


def extraction(**kw):
    base = dict(people=[{"first_name": "Braden", "last_name": "Peters", "role": "charged", "evidence": "Braden Peters"}],
                incident_date="2026-02-02", arrest_date="2026-03-26", county="Osceola", state="FL",
                agency="Fort Lauderdale Police Department",
                charges=["battery", "conspiracy to commit battery", "misdemeanor battery"])
    base.update(kw)
    return Extraction.model_validate(base)


def article(text=PETERS_TEXT):
    return Article(url="https://www.fox35orlando.com/news/braden-peters", title="Braden Peters arrested",
                   text=text, published=date(2026, 3, 27), via="direct")


class WarrantCountyTest(unittest.TestCase):
    def test_detects_the_warrant_county(self):
        self.assertEqual(warrant_county(PETERS_TEXT), "osceola")
        self.assertEqual(warrant_county("deputies arrested him on an Osceola County warrant"), "osceola")
        self.assertEqual(warrant_county("held on a warrant issued by the St. Lucie County Sheriff"), "st_lucie")
        self.assertIsNone(warrant_county("deputies served a search warrant at the home in Kissimmee"))
        self.assertIsNone(warrant_county("A Broward County judge signed the warrant"))
        self.assertEqual(warrant_county("Lentz was arrested on an active felony warrant from Osceola County"), "osceola")
        # Otra orden pendiente, no la del arresto de la nota.
        self.assertIsNone(warrant_county("Deputies arrested him for burglary. He also had a warrant out of Polk County."))

    def test_arresting_agency_from_another_county_is_not_used(self):
        aq = build_query(extraction(), article())
        self.assertEqual(aq.query.county, "osceola")
        self.assertIsNone(aq.query.agency)
        self.assertTrue(any("Fort Lauderdale" in n for n in aq.notes))

    def test_the_warrant_county_wins_over_the_agency(self):
        """Lo detuvo el Sheriff de Orange con una orden de Osceola: el caso está en Osceola."""
        aq = build_query(extraction(agency="Orange County Sheriff's Office", county="Orange"),
                         article("Orange County deputies arrested Peters on a warrant out of Osceola County."))
        self.assertEqual((aq.query.county, aq.query.agency), ("osceola", None))

    def test_agency_of_the_warrant_county_stays(self):
        aq = build_query(extraction(agency="Osceola County Sheriff's Office"),
                         article("Osceola deputies arrested Peters on a warrant out of Osceola County."))
        self.assertEqual(aq.query.agency, "Osceola County Sheriff's Office")

    def test_without_a_warrant_nothing_changes(self):
        aq = build_query(extraction(agency="Osceola County Sheriff's Office"), article("Deputies arrested Peters."))
        self.assertEqual((aq.query.county, aq.query.agency), ("osceola", "Osceola County Sheriff's Office"))


class PetersMatchTest(unittest.TestCase):
    def test_conspiracy_category(self):
        self.assertEqual(categorize(["CRIMINAL CONSPIRACY MISDEMEANOR SECOND (777.04 3)"]), {"conspiracy"})
        self.assertIn("conspiracy", categorize(["conspiracy to commit battery"]))

    def test_peters_is_found(self):
        case = CaseDetails(CaseCandidate("2026 MM 000546", "PETERS, BRADEN ERIC", "MM", "OPEN", None,
                                         date(2026, 3, 10), "x"),
                           [Charge(date(2026, 2, 2), "CRIMINAL CONSPIRACY MISDEMEANOR SECOND (777.04 3)", None,
                                   date(2026, 3, 27), "OSCEOLA COUNTY SHERIFF'S OFFICE", "26I012324",
                                   initial_date=date(2026, 3, 10))], [])
        q = build_query(extraction(), article()).query
        match = score_case(q, case)
        self.assertEqual(pick_best([(match, case)])[1], "found", match.reasons)
        self.assertIn("cargos coinciden (conspiracy)", match.reasons)


if __name__ == "__main__":
    unittest.main()
