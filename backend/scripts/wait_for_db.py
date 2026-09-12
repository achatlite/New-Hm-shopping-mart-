import os,time,mysql.connector
for i in range(60):
    try:
        c=mysql.connector.connect(host=os.getenv("DB_HOST","mysql"),port=int(os.getenv("DB_PORT","3306")),user=os.getenv("DB_USER","Sidd"),password=os.getenv("DB_PASSWORD","Sidd@123"),database=os.getenv("DB_NAME","hm_shopping_mart"))
        c.close(); print("MySQL ready"); break
    except Exception as e:
        print("Waiting for MySQL",i+1,e); time.sleep(2)
else: raise SystemExit("MySQL unavailable")
