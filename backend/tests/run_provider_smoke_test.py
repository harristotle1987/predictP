import asyncio
from backend.providers.provider_router import provider_router

async def main():
    print("\n" + "=" * 75)
    print("           PREDICTPRO PROVIDER LAYER SMOKE TEST REPORT")
    print("=" * 75)
    
    reports = await provider_router.run_smoke_test()
    
    for r in reports:
        print(f"\nSPORT:             {r['sport'].upper()}")
        print(f"PROVIDER:          {r['provider']}")
        print(f"ENDPOINT/METHOD:   {r['endpoint/method']}")
        print(f"FIXTURES RETURNED: {r['fixtures returned']}")
        print(f"FIRST FIXTURE:     {r['first fixture']}")
        print(f"STATUS:            {r['status']}")

    print("\n" + "=" * 75)

if __name__ == "__main__":
    asyncio.run(main())
