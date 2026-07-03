"""Superuser admin services: per-client profile (plan override, custom pricing,
suspend, notes), money-in-INR derivation, and the admin audit log.

The per-client admin settings live in ONE ``org_configurations`` JSON record
(key ``ADMIN_PROFILE``); prices/plan fall back to global defaults when unset.
"""
