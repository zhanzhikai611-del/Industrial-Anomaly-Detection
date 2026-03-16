// Playwright script
const { chromium } = require('playwright');

(async () => {
    try {
        const browser = await chromium.launch();
        const page = await browser.newPage();
        
        // Disable images/css for speed
        await page.route('**/*.(css|png|jpg|jpeg|webp)', route => route.abort());

        // We need to login if redirected
        await page.goto('http://127.0.0.1:8000/dashboard/');
        
        // Wait for potential login form
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
        
        const hasAddUsername = await page.evaluate(() => !!document.getElementById('add-username'));
        console.log("Has #add-username after HTMX swap:", hasAddUsername);
        
        if (!hasAddUsername) {
            const oobExists = await page.evaluate(() => !!document.getElementById('global-modals-area'));
            console.log("Does #global-modals-area exist?", oobExists);
            
            const oobInMain = await page.evaluate(() => document.getElementById('main-content').innerHTML.includes('global-modals-area'));
            console.log("Is global-modals-area STUCK inside main-content?", oobInMain);
        }
        
        await browser.close();
    } catch(e) {
        console.log("Error:", e.message);
    }
})();
