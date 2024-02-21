# CHANGELOG

All notable changes to this project will be documented in this file.

## v0.2

* Add `Visitor.expires_at` timestamp to manage expiry
* Add `Visitor.is_active` switch to allow instant disabling of visitor passes
* Add `Visitor.validate()` method to validate pass
* Add `VISITOR_TOKEN_EXPIRY` setting (default: 300s)
* Add `VISITOR_SESSION_EXPIRY` settings (default: 0s - expires on browse close)

## [Unreleased]

* Add `Visitor.max_uses` and `Visitor.uses_remaining` fields to manage optional usage limits
* Add `VisitorManager` with a method to handle tracking and updating Visitor usage limits
* Update `VisitorRequestMiddleware` to use the new manager and handle usage limits where applicable
* Update `Visitor.validate()` to also check usage limits
* Update `Visitor.reactivate()` to reset usage limits
