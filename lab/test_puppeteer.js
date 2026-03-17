const { chromium } = require('playwright');
(async () => {
    try {
        const browser = await chromium.launch();
        const page = await browser.newPage();
        
        await page.goto('http://127.0.0.1:8000/dashboard/');
        
        // Login if needed
        if (await page.$('#id_username')) {
            await page.fill('#id_username', 'admin');
            await page.fill('#id_password', 'admin');
            await page.click('button[type="submit"]');
            await page.waitForNavigation();
        }

        console.log("Logged in. Navigating to accounts via HTMX");
        // Navigate via HTMX sidebar
        const a = await page.$$('a.nav-link');
        for (let l of a) {
            const h = await l.getAttribute('href');
            if (h === '/accounts/') {
                await l.click();
                console.log("Clicked accounts");
                break;
            }
        }
        
        await page.waitForTimeout(1500); // Wait for HTMX swap
        
        await page.screenshot({ path: 'screenshot_htmx.png' });
        
        const errors = await page.evaluate(() => window.errors || []);
        console.log("Errors:", errors);
        
        await browser.close();
    } catch(e) {
        console.log("Error:", e.message);
    }
})();
