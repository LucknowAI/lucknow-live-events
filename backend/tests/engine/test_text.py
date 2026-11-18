from engine.text import html_to_text


def test_html_to_text_keeps_text_and_drops_code():
    html = (
        "<p>Hello &amp; welcome</p><script>alert(1)</script><style>p{}</style><ul><li>One</li></ul>"
    )

    assert html_to_text(html) == "Hello & welcome\nOne"
