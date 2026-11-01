"""Drive the add-on UI end to end against the mock Home Assistant and take screenshots."""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE, OUT, MOCK = sys.argv[1], sys.argv[2], sys.argv[3]
errors = []


def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)


def mock_set(entity, state):
    req = urllib.request.Request(MOCK + "/test/set", data=json.dumps({"entity_id": entity, "state": state}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req).read()


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(BASE)
    page.wait_for_selector("#view-mainlines:not([hidden])")

    page.click("text=+ Add mainline")
    page.click("text=+ Add pump / main valve")
    page.fill("#view-mainlines input.ent", "switch.pump")
    page.press("#view-mainlines input.ent", "Tab")
    shot(page, "01-mainlines")

    page.click("#tabs button[data-tab=blocks]")
    names = ["Citrus North", "Citrus South", "Vines A", "Vines B", "Pivot 1", "Nursery"]
    for i, n in enumerate(names):
        page.click("text=+ Add block")
        page.locator(".modal .form-grid input[type=text]").nth(0).fill(n)
        page.locator(".modal .form-grid input[type=text]").nth(1).fill(f"switch.valve_{i + 1}")
        page.locator(".modal .form-grid input[type=number]").nth(1).fill("1")
        page.locator(".modal .form-grid input[type=number]").nth(2).fill("30")
        page.click(".modal .foot button.primary")
    shot(page, "02-blocks-unsaved")

    page.click("#tabs button[data-tab=programs]")
    page.click("text=Create a program")
    page.fill(".editor-head input.name", "Summer drip")
    page.press(".editor-head input.name", "Tab")
    page.click("td.blocks")
    page.locator(".pick").nth(0).click(); page.locator(".pick").nth(1).click()
    page.click(".modal >> text=Done")
    page.click("text=+ Add step")
    page.locator("td.blocks").nth(1).click()
    page.locator(".pick").nth(2).click(); page.locator(".pick").nth(3).click()
    page.click(".modal >> text=Done")
    page.click("text=+ Add step")
    page.locator("td.blocks").nth(2).click()
    page.locator(".pick").nth(4).click()
    page.click(".modal >> text=Done")
    page.locator("tr").nth(3).locator("input[aria-label=hours]").fill("0")
    page.locator("tr").nth(3).locator("input[aria-label=minutes]").fill("0")
    time.sleep(0.8)
    shot(page, "03-problem-listed")
    page.locator("tr").nth(3).locator("input[aria-label=minutes]").fill("45")
    time.sleep(0.6)

    page.click("text=← All programs")
    page.click("text=+ New program")
    page.fill(".editor-head input.name", "Night loop")
    page.press(".editor-head input.name", "Tab")
    page.click(".seg >> text=Loop constantly")
    page.click("td.blocks")
    page.locator(".pick").nth(5).click()
    page.click(".modal >> text=Done")
    time.sleep(0.8)
    page.click("#save")
    time.sleep(1)
    shot(page, "04-saved")

    page.click("#tabs button[data-tab=live]")
    time.sleep(1)
    page.locator(".mlsec select").select_option(label="Summer drip")
    page.click(".mlsec .idle-start button")
    time.sleep(2.5)
    page.locator(".mlsec select").select_option(label="Night loop")
    page.click(".mlsec .idle-start button")
    time.sleep(8)
    shot(page, "05-live-two-running")

    page.click("#tabs button[data-tab=blocks]")
    time.sleep(2.5)
    shot(page, "06-blocks-live")
    mock_set("switch.valve_3", "on")      # valve 3 opened behind our back
    time.sleep(3)
    shot(page, "07-blocks-out-of-sync")
    page.locator("#block-table tbody tr").nth(2).locator("text=Close").click()
    time.sleep(2.5)

    page.click("#tabs button[data-tab=live]")
    time.sleep(1)
    page.locator(".run").first.locator("text=Pause").click()
    time.sleep(2.5)
    shot(page, "08-paused")
    page.emulate_media(color_scheme="dark")
    time.sleep(2)
    shot(page, "09-live-dark")

    page.emulate_media(color_scheme="light")
    page.click("#tabs button[data-tab=programs]")
    time.sleep(2.5)
    shot(page, "11-programs-list")
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(BASE)
    time.sleep(3)
    shot(m, "10-mobile-live")
    m.click("#tabs button[data-tab=blocks]")
    time.sleep(2.5)
    shot(m, "12-mobile-blocks")
    m.click("#tabs button[data-tab=programs]")
    time.sleep(1)
    m.click("text=✎ Edit")
    time.sleep(0.5)
    shot(m, "13-mobile-editor")
    b.close()
print("JS errors:", errors)
