from core import icp


def test_cost_counts_whole_pages():
    assert icp.cost(25) == 0.1 and icp.cost(26) == 0.2
    assert icp.cost(100, "Full + email search") == 1.4


def test_build_input_drops_unknown_codes():
    inp = icp.build_input({"titles": "Founder, CEO", "locations": "Israel", "headcount": ["C", "Z"], "query": "real estate"}, 50)
    assert inp["currentJobTitles"] == ["Founder", "CEO"] and inp["companyHeadcount"] == ["C"]
    assert inp["takePages"] == 2 and inp["maxItems"] == 50 and inp["searchQuery"] == "real estate"
    assert "seniorityLevelIds" not in inp


def test_rows_fallbacks_and_formula_guard():
    r = icp.rows([{"firstName": "Dana", "lastName": "Levi", "headline": "=cmd", "location": {"linkedinText": "Tel Aviv"},
                   "currentPosition": [{"companyName": "Acme"}], "linkedinUrl": "https://www.linkedin.com/in/dana"}, {}])
    assert r[0]["name"] == "Dana Levi" and r[0]["company"] == "Acme" and r[0]["location"] == "Tel Aviv" and r[0]["headline"] == "'=cmd"
    assert r[1]["name"] == "לא נמצא" and list(r[1]) == icp.COLUMNS
