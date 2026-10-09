import { test, expect } from '@playwright/test'

test('real saved investigation opens, plots render and evidence is accessible', async ({page}) => {
  const errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.goto('/')
  await page.getByRole('button',{name:/WASP-18 TIC/}).first().click()
  await expect(page.getByRole('heading',{name:'WASP-18'})).toBeVisible()
  await expect(page.getByRole('img',{name:'Original and detrended TESS light curves'}).locator('.plot-container')).toBeVisible()
  await page.getByRole('button',{name:'Evidence',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Scientific checks'})).toBeVisible()
  await expect(page.getByText('Statistical false-positive probability',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Sources',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Data provenance'})).toBeVisible()
  await expect(page.getByRole('link',{name:/tess.*fits/}).first()).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button',{name:/WASP-18 TIC/}).first()).toBeVisible()
  expect(errors).toEqual([])
})

test('configuration, target search and queue controls expose real state', async ({page})=>{
  await page.goto('/')
  await page.getByRole('button',{name:'AI settings',exact:true}).click()
  await expect(page.getByRole('combobox',{name:'Provider',exact:true})).toBeVisible()
  await expect(page.getByLabel('API key',{exact:false})).toHaveAttribute('type','password')
  await page.getByRole('button',{name:'New investigation',exact:true}).click()
  await expect(page.getByRole('heading',{name:'New investigation'})).toBeVisible()
  await page.getByRole('button',{name:'Find observations',exact:true}).click()
  await expect(page.getByText(/calibrated products found/)).toBeVisible({timeout:60000})
  await page.getByRole('button',{name:'Close new investigation'}).click()
  await page.getByRole('button',{name:'Research queue',exact:false}).first().click()
  await expect(page.getByRole('heading',{name:/Research queue/})).toBeVisible()
  await expect(page.getByRole('button',{name:/WASP-18/}).first()).toBeVisible()
})

test('narrow viewport has usable navigation and no horizontal document overflow',async({page})=>{
  await page.setViewportSize({width:800,height:1000})
  await page.goto('/')
  await expect(page.getByRole('button',{name:'New investigation',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy()
})
