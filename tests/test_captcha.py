"""CAPTCHA detection tests — Cloudflare, hCaptcha, reCAPTCHA, generic, negatives."""

from unittest.mock import MagicMock

from mcp_research.captcha import detect_captcha, CaptchaResult


def _mock_resp(status_code=200, server="", url="https://example.com"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url
    resp.headers = {"Server": server}
    return resp


class TestCloudflareDetection:

    def test_cf_challenge_page(self):
        resp = _mock_resp(403, server="cloudflare")
        result = detect_captcha(resp, '<title>Just a moment...</title><div class="cf-challenge-running">')
        assert result.detected is True
        assert result.provider == "cloudflare"

    def test_cf_chl_opt(self):
        resp = _mock_resp(503, server="cloudflare")
        result = detect_captcha(resp, '<script>var _cf_chl_opt={}</script>')
        assert result.detected is True
        assert result.provider == "cloudflare"

    def test_cf_cdn_cgi(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js">')
        assert result.detected is True
        assert result.provider == "cloudflare"


class TestHCaptchaDetection:

    def test_hcaptcha_script(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<script src="https://hcaptcha.com/1/api.js"></script>')
        assert result.detected is True
        assert result.provider == "hcaptcha"


class TestReCaptchaDetection:

    def test_recaptcha_script(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<script src="https://google.com/recaptcha/api.js"></script>')
        assert result.detected is True
        assert result.provider == "recaptcha"

    def test_recaptcha_response_field(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<textarea name="g-recaptcha-response"></textarea>')
        assert result.detected is True
        assert result.provider == "recaptcha"


class TestAkamaiDetection:

    def test_akamai_bot_manager(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<div>Akamai Bot Manager detected suspicious activity</div>')
        assert result.detected is True
        assert result.provider == "akamai"


class TestGenericDetection:

    def test_access_denied(self):
        resp = _mock_resp(403, server="cloudflare")
        result = detect_captcha(resp, '<h1>Access Denied</h1>')
        assert result.detected is True

    def test_are_you_a_robot(self):
        resp = _mock_resp(403)
        result = detect_captcha(resp, '<p>Are you a robot? Please verify.</p>')
        assert result.detected is True

    def test_waf_server_403_no_body_sigs(self):
        resp = _mock_resp(403, server="cloudflare")
        result = detect_captcha(resp, "<html><body>Forbidden</body></html>")
        assert result.detected is True
        assert result.provider == "generic"


class TestNegatives:

    def test_200_not_detected(self):
        resp = _mock_resp(200)
        result = detect_captcha(resp, "<html><body>Normal page</body></html>")
        assert result.detected is False

    def test_404_not_detected(self):
        resp = _mock_resp(404)
        result = detect_captcha(resp, "<html><body>Not Found</body></html>")
        assert result.detected is False

    def test_403_without_captcha_sigs(self):
        resp = _mock_resp(403, server="nginx")
        result = detect_captcha(resp, "<html><body>Forbidden</body></html>")
        assert result.detected is False

    def test_empty_body(self):
        resp = _mock_resp(403, server="cloudflare")
        result = detect_captcha(resp, "")
        # WAF header with 403 triggers generic detection
        assert result.detected is True


class TestCaptchaResultBool:

    def test_truthy_when_detected(self):
        r = CaptchaResult(True, "cloudflare", "https://x.com", "hint")
        assert r

    def test_falsy_when_not_detected(self):
        r = CaptchaResult(False, None, "https://x.com", "")
        assert not r
