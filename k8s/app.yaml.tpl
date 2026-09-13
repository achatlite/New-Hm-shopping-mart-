apiVersion: v1
kind: Namespace
metadata:
  name: hm-shopping-mart
---
apiVersion: v1
kind: Secret
metadata:
  name: hm-shopping-mart-db
  namespace: hm-shopping-mart
type: Opaque
stringData:
  MYSQL_ROOT_PASSWORD: root_password_change_me
  MYSQL_PASSWORD: Sidd@123
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: hm-shopping-mart-mysql-logging
  namespace: hm-shopping-mart
data:
  99-logging.cnf: |
    [mysqld]
    log_error=/var/log/mysql/error.log
    general_log=ON
    general_log_file=/var/log/mysql/general.log
    slow_query_log=ON
    slow_query_log_file=/var/log/mysql/slow.log
    long_query_time=1
---
apiVersion: v1
kind: Service
metadata:
  name: mysql
  namespace: hm-shopping-mart
spec:
  selector:
    app: hm-shopping-mart-mysql
  ports:
    - port: 3306
      targetPort: 3306
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hm-shopping-mart-mysql
  namespace: hm-shopping-mart
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: hm-shopping-mart-mysql
  template:
    metadata:
      labels:
        app: hm-shopping-mart-mysql
    spec:
      containers:
        - name: mysql
          image: mysql:8.0
          imagePullPolicy: IfNotPresent
          env:
            - name: MYSQL_DATABASE
              value: hm_shopping_mart
            - name: MYSQL_USER
              value: Sidd
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: hm-shopping-mart-db
                  key: MYSQL_PASSWORD
            - name: MYSQL_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: hm-shopping-mart-db
                  key: MYSQL_ROOT_PASSWORD
          ports:
            - containerPort: 3306
          readinessProbe:
            exec:
              command:
                - sh
                - -c
                - mysqladmin ping -h 127.0.0.1 -u root -p"$${MYSQL_ROOT_PASSWORD}" --silent
            initialDelaySeconds: 20
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 6
          livenessProbe:
            exec:
              command:
                - sh
                - -c
                - mysqladmin ping -h 127.0.0.1 -u root -p"$${MYSQL_ROOT_PASSWORD}" --silent
            initialDelaySeconds: 300
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          volumeMounts:
            - name: data
              mountPath: /var/lib/mysql
            - name: host-timezone
              mountPath: /etc/localtime
              readOnly: true
            - name: init
              mountPath: /docker-entrypoint-initdb.d
              readOnly: true
            - name: mysql-logs
              mountPath: /var/log/mysql
            - name: mysql-config
              mountPath: /etc/mysql/conf.d/99-logging.cnf
              subPath: 99-logging.cnf
              readOnly: true
      volumes:
        - name: data
          hostPath:
            path: ${HM_PROJECT_ROOT}/k8s/mysql-data
            type: DirectoryOrCreate
        - name: init
          hostPath:
            path: ${HM_PROJECT_ROOT}/k8s/mysql-init
            type: Directory
        - name: mysql-logs
          hostPath:
            path: ${HM_PROJECT_ROOT}/logs/database
            type: DirectoryOrCreate
        - name: mysql-config
          configMap:
            name: hm-shopping-mart-mysql-logging
        - name: host-timezone
          hostPath:
            path: /etc/localtime
            type: File
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hm-shopping-mart-backend
  namespace: hm-shopping-mart
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: hm-shopping-mart-backend
  template:
    metadata:
      labels:
        app: hm-shopping-mart-backend
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/path: "/metrics"
        prometheus.io/port: "8000"
    spec:
      containers:
        - name: backend
          image: ${BACKEND_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: DB_HOST
              value: mysql
            - name: DB_PORT
              value: "3306"
            - name: DB_NAME
              value: hm_shopping_mart
            - name: DB_USER
              value: Sidd
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: hm-shopping-mart-db
                  key: MYSQL_PASSWORD
            - name: LOG_DIR
              value: /app/logs
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 6
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          startupProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 5
            failureThreshold: 30
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          volumeMounts:
            - name: logs
              mountPath: /app/logs
            - name: host-timezone
              mountPath: /etc/localtime
              readOnly: true
      volumes:
        - name: logs
          hostPath:
            path: ${HM_PROJECT_ROOT}/logs/backend
            type: DirectoryOrCreate
        - name: host-timezone
          hostPath:
            path: /etc/localtime
            type: File
---
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: hm-shopping-mart
spec:
  selector:
    app: hm-shopping-mart-backend
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hm-shopping-mart-frontend
  namespace: hm-shopping-mart
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: hm-shopping-mart-frontend
  template:
    metadata:
      labels:
        app: hm-shopping-mart-frontend
    spec:
      containers:
        - name: frontend
          image: ${FRONTEND_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 80
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 20
            periodSeconds: 20
          resources:
            requests:
              cpu: 50m
              memory: 32Mi
            limits:
              cpu: 250m
              memory: 128Mi
          volumeMounts:
            - name: logs
              mountPath: /var/log/nginx
      volumes:
        - name: logs
          hostPath:
            path: ${HM_PROJECT_ROOT}/logs/frontend
            type: DirectoryOrCreate
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: hm-shopping-mart
spec:
  type: NodePort
  selector:
    app: hm-shopping-mart-frontend
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080
