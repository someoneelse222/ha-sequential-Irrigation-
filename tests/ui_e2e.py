"""Drive the add-on UI end to end against the mock Home Assistant and take screenshots."""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1]
OUT = sys.argv[2]
errors = []


def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 860})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(BASE)
    page.wait_for_selector("#view-mainlines:not([hidden])")
    shot(page, "01-empty")

    # mainline
    page.click("text=+ Add mainline")
    page.click("text=+ Add pump / main valve")
    page.fill("#view-mainlines input.ent", "switch.pump")
    page.press("#view-mainlines input.ent", "Tab")
    page.click("text=+ Add mainline")
    shot(page, "02-mainlines")

    # blocks
    page.click("#tabs button[data-tab=blocks]")
    page.fill("#view-blocks .toolbar input[type=number]", "6")
    page.click("#view-blocks .toolbar button.primary")
    ents = page.locator("#view-blocks input.ent")
    for i in range(6):
        ents.nth(i).fill(f"switch.valve_{i + 1}")
        ents.nth(i).press("Tab")
    names = ["Citrus North", "Citrus South", "Vines A", "Vines B", "Pivot 1", "Nursery"]
    for i, n in enumerate(names):
        page.locator("#view-blocks tbody tr").nth(i).locator("input[type=text]").first.fill(n)
    shot(page, "03-blocks")

    # program
    page.click("#tabs button[data-tab=programs]")
    page.click("text=Create a program")
    page.fill(".editor-head input.name", "Summer drip")
    page.press(".editor-head input.name", "Tab")
    # step 1 blocks 1,2
    page.click("td.blocks")
    page.locator(".pick").nth(0).click()
    page.locator(".pick").nth(1).click()
    page.click(".modal >> text=Done")
    # duration 4h
    page.locator("tr").nth(1).locator("input[aria-label=hours]").fill("4")
    page.locator("tr").nth(1).locator("input[aria-label=minutes]").fill("0")
    page.click("text=+ Add step")
    page.locator("td.blocks").nth(1).click()
    page.locator(".pick").nth(2).click()
    page.locator(".pick").nth(3).click()
    page.click(".modal >> text=Done")
    page.locator("tr").nth(2).locator("input[aria-label=hours]").fill("3")
    page.locator("tr").nth(2).locator("input[aria-label=minutes]").fill("30")
    page.click("text=+ Add step")
    page.locator("td.blocks").nth(2).click()
    page.locator(".pick").nth(4).click()
    page.click(".modal >> text=Done")
    page.locator("tr").nth(3).locator("input[aria-label=hours]").fill("2")
    page.click("text=+ Add start time")
    page.locator(".start-row input[type=time]").nth(1).fill("18:00")
    page.locator(".start-row input[type=time]").nth(1).press("Tab")
    time.sleep(0.6)
    shot(page, "04-program-once")

    # a step that is too long for its block (max 5h) -> problem shown
    page.locator("tr").nth(1).locator("input[aria-label=hours]").fill("6")
    time.sleep(0.8)
    page.click("#issues-toggle")
    shot(page, "05-problem")
    page.locator("tr").nth(1).locator("input[aria-label=hours]").fill("4")
    time.sleep(0.8)

    # second program: loop until
    page.click("text=+ New program")
    page.fill(".editor-head input.name", "Night loop")
    page.press(".editor-head input.name", "Tab")
    page.click(".seg >> text=Loop until a set time")
    page.click("td.blocks")
    page.locator(".pick").nth(5).click()
    page.click(".modal >> text=Done")
    page.locator("tr").nth(1).locator("input[aria-label=hours]").fill("1")
    time.sleep(0.8)
    shot(page, "06-program-until")

    # save -> setup not finished
    page.click("#save")
    page.wait_for_selector("#view-setup:not([hidden])")
    shot(page, "07-setup")
    page.click("#view-setup button.btn.primary")
    time.sleep(1)
    shot(page, "08-setup-connected")

    # save again (now ready) -> applied; mock starts a program running
    page.click("#tabs button[data-tab=programs]")
    page.fill(".editor-head input.name", "Night loop 2")
    page.press(".editor-head input.name", "Tab")
    page.click("#save")
    time.sleep(1.0)
    if page.is_visible("#modal"):
        shot(page, "08b-confirm-running")
        page.click("#modal .btn.danger")
        time.sleep(1.0)
    page.click("#tabs button[data-tab=live]")
    time.sleep(3.5)
    shot(page, "09-live")

    page.emulate_media(color_scheme="dark")
    time.sleep(0.5)
    shot(page, "10-live-dark")
    page.click("#tabs button[data-tab=programs]")
    page.locator(".pitem").first.click()
    shot(page, "11-program-dark")
    page.emulate_media(color_scheme="light")

    # mobile
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(BASE)
    time.sleep(2.5)
    shot(m, "12-mobile-live")
    m.click("#tabs button[data-tab=programs]")
    shot(m, "13-mobile-programs")
    b.close()

print("JS errors:", errors)
