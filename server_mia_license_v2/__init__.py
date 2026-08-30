"""Deployable MIA-only license V2 service package."""

from .mia_license_v2 import MiaLicenseService, LicenseServiceError

__all__ = ["MiaLicenseService", "LicenseServiceError"]
