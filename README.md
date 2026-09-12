# HM Shopping Mart — Docker-first e-commerce MVP

## URLs
- Customer: http://localhost:8080/
- Customer login: http://localhost:8080/login.html
- Create user: http://localhost:8080/register.html
- Forgot password: http://localhost:8080/forgot.html
- Admin: http://localhost:8080/admin/login.html
- Admin dashboard: http://localhost:8080/admin/dashboard.html

## Required behavior
- Customer and admin login are separate.
- Admin accounts are manually created from the database using `scripts/create_admin.py`.
- Admin cannot buy products. Backend enforces this.
- An admin can edit/delete only products owned by that admin's seller record.
- Customer can create an account directly from the login popup when attempting to buy while logged out.
- Checkout requires recipient name, phone, address lines, city, state, PIN code and country.
- Forgot password directly resets the password after verifying the account identifier.
- Password fields use an eye button to show/hide the password.
- Back button is present on standalone auth/checkout/admin screens.
- Application logs rotate at 1 MB and completed-day logs are zipped; ZIP retention is 5 days.
- Database schema contains 25 tables, including return requests and server-backed carts.

## Start
`docker compose up --build -d`

## Create an admin
`docker compose exec backend python scripts/create_admin.py admin@example.com 'AdminPassword'`

## Logs
`docker compose logs -f backend`


### Product images and discounts
Admin can paste a direct product image URL and preview it. Products support MRP, discount percentage, and selling price; the frontend displays the discount automatically.

## CI/CD and Kubernetes

The project is prepared for a Jenkins-driven Docker → Kubernetes pipeline.

### Flow
`GitHub -> Jenkins -> Docker build -> Container Registry -> Kubernetes -> Prometheus -> Grafana`

### Kubernetes application
```bash
./k8s/deploy.sh
kubectl get pods -n hm-shopping-mart
kubectl get svc -n hm-shopping-mart
```

Customer application NodePort: `30080`.

### Monitoring
```bash
./k8s/monitoring/install.sh
kubectl get pods -n monitoring
```

- Prometheus: NodePort `30090`
- Grafana: NodePort `30300`
- Backend metrics: `/metrics`
- Backend health: `/health`
- Backend readiness: `/ready`

### Jenkins
See `Jenkinsfile` and `jenkins/README.md`.

**Data safety:** deployment scripts do not run `docker compose down -v`, delete MySQL data, or recreate the existing application database. Existing products/accounts remain untouched. The MySQL init script is only consumed by MySQL on a fresh empty data directory.
