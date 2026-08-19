import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_interface_matches_public_scope(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("96-bin colour", html)
        self.assertIn("wHash", html)
        self.assertIn("Upload two videos", html)
        self.assertIn("Use two TikTok links", html)
        self.assertIn("Nepal Police Cyber Bureau", html)
        self.assertIn("Pretending to Be Someone", html)
        self.assertIn("Copyright Act, 2059 (2002)", html)
        self.assertIn("Privacy Act, 2075", html)
        self.assertIn("three months", html)
        self.assertIn("TikTok copyright form", html)
        self.assertIn("Bring facts, not only a score", html)
        self.assertIn("cyberbureau.nepalpolice.gov.np/report-cyber-crime", html)
        self.assertIn("nepalcopyright.gov.np", html)
        self.assertIn("does not determine copyright infringement", html)
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("This analysis session was lost", javascript)
        self.assertIn("job.status === 'error'", javascript)
        self.assertIn("failAnalysis(job.message", javascript)
        self.assertIn("Evidence gate", javascript)
        self.assertNotIn("Raw Hamming similarities", javascript)

        launcher = (ROOT / "start.bat").read_text(encoding="utf-8")
        self.assertIn("LISTENING", launcher)
        self.assertIn("A second copy was not started", launcher)

        windows_reset = (ROOT / "reset.ps1").read_text(encoding="utf-8")
        linux_reset = (ROOT / "reset.sh").read_text(encoding="utf-8")
        for reset_script in (windows_reset, linux_reset):
            self.assertIn(".venv", reset_script)
            self.assertIn(".bootstrap", reset_script)
            self.assertIn(".env", reset_script)
            self.assertIn("logs", reset_script)
            self.assertIn("__pycache__", reset_script)
            self.assertIn(".gitkeep", reset_script)
        self.assertIn("StartsWith($rootPrefix", windows_reset)
        self.assertIn('case "$target" in', linux_reset)

    def test_reset_click_does_not_render_pointer_event(self):
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        # Browser click handlers receive a PointerEvent argument. Keep reset
        # wrapped so that event objects can never be displayed as an error.
        self.assertIn("addEventListener('click', () => resetAnalyzer())", javascript)
        self.assertNotIn("addEventListener('click', resetAnalyzer)", javascript)
        self.assertIn("typeof notice === 'string'", javascript)


if __name__ == "__main__":
    unittest.main()
