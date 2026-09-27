import copy
import unittest
from field_brain.site_link_plan import plan_site_links


class SiteLinkPlanTests(unittest.TestCase):
    def test_repeated_schedules_do_not_create_duplicate_group_candidates(self):
        local = [{"id": "a", "address_text": "가상로 1  101호"}]
        rows = [{"site": "group-a", "address": "가상로 1 101호"}] * 3
        before = copy.deepcopy((local, rows))
        result = plan_site_links(local, rows)
        self.assertEqual(result[0]["reason"], "unique_address_candidate")
        self.assertEqual(result[0]["online_group_candidates"], ["group-a"])
        self.assertFalse(result[0]["automatic_transfer_allowed"])
        self.assertEqual((local, rows), before)

    def test_multiple_local_contracts_never_collapse_by_address(self):
        local = [{"id": i, "address_text": "가상로 1"} for i in ("a", "b")]
        result = plan_site_links(local, [{"site": "x", "address": "가상로 1"}])
        self.assertTrue(all(r["reason"] == "multiple_local_sites_at_address" for r in result))

    def test_distinct_online_groups_are_not_silently_chosen(self):
        result = plan_site_links([{"id": "a", "address_text": "가상로 1"}],
            [{"site": g, "address": "가상로 1"} for g in ("x", "y")])
        self.assertEqual(result[0]["reason"], "multiple_online_groups_at_address")

    def test_conflicting_or_incomplete_group_is_not_candidate(self):
        for other in ("다른로 2", ""):
            result = plan_site_links([{"id": "a", "address_text": "가상로 1"}],
                [{"site": "x", "address": "가상로 1"}, {"site": "x", "address": other}])
            self.assertEqual(result[0]["reason"], "inconsistent_online_group")

    def test_unit_numbers_and_missing_addresses_are_not_guessed(self):
        result = plan_site_links([{"id": "a", "address_text": "가상로 1 101호"},
            {"id": "b", "address_text": None}], [{"site": "x", "address": "가상로 1 102호"}])
        self.assertEqual([r["reason"] for r in result],
            ["no_exact_address_match", "missing_local_address"])

    def test_deleted_sites_excluded_invalid_identifiers_rejected(self):
        self.assertEqual(plan_site_links([{"id": "a", "deleted_at": "2026-01-01"}], []), [])
        with self.assertRaises(ValueError):
            plan_site_links([{"id": "a"}, {"id": "a"}], [])
        with self.assertRaises(ValueError):
            plan_site_links([], [{"address": "가상로 1"}])
