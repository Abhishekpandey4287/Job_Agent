"""Tests for discovery keywords."""

from job_agent.discovery.keywords import matches_location, matches_track


def test_matches_android_track():
    assert matches_track("Senior Android Engineer", "Kotlin Jetpack Compose")
    assert matches_track("Java Backend Developer", "Spring Boot microservices")


def test_matches_location_india():
    assert matches_location("Bengaluru, India", "", "India")
    assert matches_location("Mumbai", "", "Mumbai")


def test_matches_location_remote():
    assert matches_location("Remote - India", "", "Remote")
