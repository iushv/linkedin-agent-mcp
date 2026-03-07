from linkedin_mcp_server.core.schemas import (
    JobCard,
    PersonCard,
    is_valid_job_card,
    is_valid_person_card,
)


class TestPersonCardValidity:
    def test_valid_when_name_and_profile_url_present(self):
        card = PersonCard(
            name="Priya Sharma",
            profile_url="https://linkedin.com/in/priya",
        )
        assert is_valid_person_card(card)

    def test_invalid_without_name(self):
        card = PersonCard(name="", profile_url="https://linkedin.com/in/priya")
        assert not is_valid_person_card(card)

    def test_invalid_without_profile_url(self):
        card = PersonCard(name="Priya Sharma", profile_url="")
        assert not is_valid_person_card(card)


class TestJobCardValidity:
    def test_valid_with_title_company_and_location(self):
        card = JobCard(title="Data Engineer", company="Astek", location="Singapore")
        assert is_valid_job_card(card)

    def test_valid_with_posting_date_only(self):
        card = JobCard(
            title="Data Engineer",
            company="Astek",
            posting_date="1 week ago",
        )
        assert is_valid_job_card(card)

    def test_invalid_without_title(self):
        card = JobCard(title="", company="Astek", location="Singapore")
        assert not is_valid_job_card(card)

    def test_invalid_without_company(self):
        card = JobCard(title="Data Engineer", company="", location="Singapore")
        assert not is_valid_job_card(card)

    def test_invalid_without_secondary_identifier(self):
        card = JobCard(title="Data Engineer", company="Astek")
        assert not is_valid_job_card(card)
