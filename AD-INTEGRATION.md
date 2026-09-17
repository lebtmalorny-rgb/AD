# Подключение AD и сопоставление групп в PVS

Инструкция относится к архиву `kolla-ansible-pvs_1.0.0_14.09.zip`.
Она показывает, что добавить в конфигурацию инсталляции Kolla, чтобы Keystone
и OpenSearch использовали группы, созданные `Create-PvsAdAccounts.ps1`.
Исходники ролей Ansible для описанного ручного способа изменять не требуется.
Для автоматизации назначений через `post-deploy` есть отдельное расширение:
[POSTDEPLOY.md](POSTDEPLOY.md).

Это инструкция и проверенные по структуре примеры. На действующем стенде
подключение AD, применение настроек и права пользователей не проверялись.
Полное соответствие ролевой модели требует проверки API-политик и операций
из раздела «Проверка результата», в частности запрета удаления ВМ для `vm_developer`.

## Где назначаются права

| Группа AD | Сервис | Назначение |
| --- | --- | --- |
| `PVS_VirtAdmins` | Keystone | `virtualization_admin`, наследующая `admin`, в согласованных областях доступа |
| `PVS_VMDevelopers` | Keystone | `vm_developer`, наследующая `member`, после настройки ограничений API |
| `PVS_SecurityAudit` | OpenSearch Security Plugin | `security_admin`: чтение журналов и работа в tenant `security-audit` |

AD хранит пользователей и членство в группах. Keystone хранит назначения ролей
на проекты и домены. OpenSearch хранит собственные роли и сопоставления групп.
Наличие пользователя с определённым именем само по себе не назначает ему права.

Keystone использует LDAP в режиме чтения: создавать и менять пользователей AD
нужно средствами AD. Требование ролевой модели об управлении пользователями
через Keystone применимо к поддерживаемому хранилищу учётных записей, а не
автоматически к внешнему AD. [Документация Keystone](https://docs.openstack.org/keystone/latest/admin/configuration.html#integrate-identity-with-ldap).

## Какие файлы подготовить

Все пути `/etc/kolla` ниже относятся к узлу запуска Kolla-Ansible.
Если используется другой `--configdir`, замените базовый каталог.
Перед изменением существующих файлов сохраните их копии; примеры нужно объединять
с имеющейся конфигурацией, а не безусловно записывать поверх неё.

| Файл на узле запуска | Что сделать |
| --- | --- |
| `/etc/kolla/globals.d/90-pvs-ad.yml` | Взять [пример](examples/90-pvs-ad.yml), заменить параметры AD |
| `/etc/kolla/globals.d/91-pvs-ad-secret.yml` | Создать зашифрованный Ansible Vault файл с паролем bind-учётной записи |
| `/etc/kolla/config/keystone/domains/keystone.AD.conf` | Взять [шаблон LDAP-домена](examples/keystone.AD.conf), изменить `suffix` |
| `/etc/kolla/config/keystone/keystone.conf` | Для связи `virtualization_admin → admin` добавить [override](examples/keystone-rbac.conf), сохранив остальные запрещённые implied roles |
| `/etc/kolla/config/opensearch/opensearch_dashboards.yml` | Добавить настройки из [примера Dashboards](examples/opensearch_dashboards.yml) |
| `/etc/kolla/certificates/ca/ad-ca.crt` | Разместить CA, подписавший сертификат LDAPS; вариант для локального источника сертификатов |
| `/etc/kolla/config/nova/policy.yaml` | Подготовить ограничения `vm_developer` для версии Nova из ваших образов |
| `/etc/kolla/config/horizon/nova_policy.yaml` | Согласовать ограничения действий Horizon с политикой Nova API |

При необходимости ограничения остальных операций модели задаются также в
`/etc/kolla/config/neutron/policy.yaml`, `cinder/policy.yaml`, `glance/policy.yaml`
и `keystone/policy.yaml`. Само создание ролей эти политики не заменяет.

## Как в архиве уже применяется policy

Механизм применения policy реализован. Для Nova цепочка при стандартных путях такая:

```text
Узел запуска: /etc/kolla/config/nova/policy.yaml
    → узел сервиса: /etc/kolla/nova-api/policy.yaml
    → контейнер nova_api: /etc/nova/policy.yaml
    → nova.conf: [oslo_policy] policy_file = policy.yaml
```

Подтверждения в исходниках:

1. `ansible/roles/nova/tasks/config.yml:12` ищет внешний файл, а задача с строки 87
   копирует его для сервисов из `nova_services_require_policy_json`.
   Несмотря на историческое имя переменной, поддерживается и YAML.
2. `ansible/roles/nova/templates/nova-api.json.j2:36` задаёт путь внутри контейнера.
3. `ansible/roles/nova/templates/nova.conf.j2:184` включает `[oslo_policy]`.
4. `ansible/roles/nova/tasks/reconfigure.yml` вызывает deploy, который выполняет
   config, проверку контейнеров и handlers. В `nova-cell` предусмотрена аналогичная
   доставка для выбранных compute-сервисов.

Форматы задаются в `ansible/group_vars/all.yml:861`: сначала `policy.yaml`,
затем `policy.json`. Выбирается первый найденный файл, а не объединение двух.
В роли Ansible копируется один policy-файл; эффективные правила сервиса могут
дополняться встроенными defaults и другими настройками конкретного образа.

У Horizon отдельная цепочка. Он ищет файлы с префиксом имени сервиса:

| Политика API на узле запуска | Соответствующий override Horizon |
| --- | --- |
| `config/nova/policy.yaml` | `config/horizon/nova_policy.yaml` |
| `config/neutron/policy.yaml` | `config/horizon/neutron_policy.yaml` |
| `config/cinder/policy.yaml` | `config/horizon/cinder_policy.yaml` |
| `config/glance/policy.yaml` | `config/horizon/glance_policy.yaml` |
| `config/keystone/policy.yaml` | `config/horizon/keystone_policy.yaml` |

`ansible/roles/horizon/tasks/policy_item.yml:7` ищет эти файлы только в
`{{ node_custom_config }}/horizon`. `tasks/config.yml:92` доставляет их на узел,
а `templates/horizon.json.j2:13` — в `/etc/openstack-dashboard/` контейнера.
`templates/_9998-kolla-settings.py.j2:136` устанавливает `POLICY_FILES_PATH`.
Сверьте также эффективный `POLICY_FILES` в образе Horizon: штатные имена перечислены
в [документации Horizon](https://docs.openstack.org/horizon/latest/configuration/settings.html#policy-files).

Автоматического копирования `config/nova/policy.yaml` в policy Horizon здесь нет.
Согласуйте нужные правила в обоих файлах, сохранив специфичные для Horizon правила.
Ограничение в Horizon управляет доступностью действий интерфейса; принудительная
проверка запросов CLI/API должна выполняться на стороне Nova и других API-сервисов.

В самом ZIP нет файлов `policy.yaml`, `policy.json`, `*_policy.yaml`, `*_policy.json`
и определений ролей `vm_developer`/`virtualization_admin`. Следовательно, механизм
доставки готов, а содержание внешних policy инсталляции и встроенных правил образов
по этому архиву не установлено. Если на узле запуска такие файлы уже есть,
сначала прочитайте их и внесите необходимые изменения в них.

## 1. Подготовить AD и параметры подключения

Запустите PowerShell-скрипт по [README](README.md). Дополнительно нужна отдельная
техническая учётная запись, например `svc_pvs_ldap`, с правом чтения пользователей,
групп и членства в нужной OU. Текущий PowerShell-скрипт эту запись не создаёт.

До применения должны быть доступны DNS и LDAPS на TCP/636 от узлов Keystone
и OpenSearch. Сертификат LDAPS должен соответствовать имени контроллера,
а его цепочка CA должна быть доверенной для обоих сервисов.

В `90-pvs-ad.yml` замените:

```yaml
pvs_ad_host: dc01.example.local
pvs_ad_base_dn: OU=PVS,DC=example,DC=local
pvs_ad_bind_dn: CN=svc_pvs_ldap,OU=ServiceAccounts,DC=example,DC=local
```

`pvs_ad_*` — переменные этих примеров, а не штатные параметры исходной Kolla.
Они подставляются в конфигурации через Jinja. Пример предполагает пользователей
и группы в одной OU, как в PowerShell-скрипте, и прямое членство без вложенных групп.
Если OU различаются, отдельно измените `user_tree_dn`/`group_tree_dn` в Keystone
и `userbase`/`rolebase` в обеих секциях LDAP OpenSearch.

Пароль не помещайте в Git. В подготовленном каталоге `globals.d` создайте файл:

```bash
ansible-vault create /etc/kolla/globals.d/91-pvs-ad-secret.yml
```

В открывшемся редакторе задайте `pvs_ad_bind_password` со значением реального
пароля bind-учётной записи в корректном YAML. Защитите локальные файлы с секретами
правами `0600`. При вызовах Kolla используйте `--ask-vault-pass` либо принятую
в вашей инсталляции настройку Vault identity. Шифрование Ansible Vault защищает
файл на узле запуска; сервисам нужен материализованный секрет в их конфигурации.

Для `kolla_secret_certificates_source: local` положите PEM-цепочку CA в
`/etc/kolla/certificates/ca/ad-ca.crt`. Пример включает `kolla_copy_ca_into_containers`;
в контейнерах CA ожидается по пути `/var/lib/kolla/share/ca-certificates/ad-ca.crt`.
Если ваша инсталляция использует источник `kv` или `pki`, сохраните его и добавьте
`ad-ca.crt` через существующий `vault_extra_ca_files` с корректными `path` и `field`.
Один локальный файл в таком режиме недостаточен: роль использует Vault-backed источник.

## 2. Подключить LDAP-домен Keystone

Разместите [keystone.AD.conf](examples/keystone.AD.conf) в
`/etc/kolla/config/keystone/domains/` и замените пример `suffix = DC=example,DC=local`.
Переменные Jinja оставьте в файле: роль Kolla обрабатывает его как шаблон.

Файл включает `driver = ldap`, проверку сертификата LDAPS, поиск пользователей
по `sAMAccountName`, групп по `cn`, членства по `member` и обработку флага
отключённой учётной записи AD. Фильтры ограничивают видимость двумя группами
OpenStack и их прямыми участниками. `PVS_SecurityAudit` в этот домен не включена.

В примере идентификаторы основаны на `sAMAccountName` и `cn`. После назначения
ролей не переименовывайте эти объекты без переноса назначений: Keystone может
получить новые идентификаторы. Для иной схемы стабильных ID сначала проверьте
совместимость выбранного атрибута с версией Keystone.

Под локальной административной учётной записью OpenStack создайте домен:

```bash
openstack domain create --or-show AD
```

Имя `AD` должно точно совпадать с `keystone.AD.conf`. Домен Keystone и DNS-домен
Active Directory — разные сущности. В примере Horizon включён режим нескольких
доменов с выбором `Default` и `AD`.

Код из архива уже копирует файлы `keystone/domains` и включает
`domain_specific_drivers_enabled`. Не нужно менять глобальный драйвер домена
`Default`: там остаются существующие локальные и сервисные учётные записи.

## 3. Настроить OpenSearch

[90-pvs-ad.yml](examples/90-pvs-ad.yml) содержит полный словарь
`opensearch_security_audit_config`, полученный из указанного архива, с изменениями:

| Вложенный файл | Изменение |
| --- | --- |
| `config.yml` | Добавлены LDAP `authc` и `authz`, проверка CA, чтение групп, включены tenants |
| `roles.yml` | Добавлена роль `security_admin` |
| `roles_mapping.yml` | Добавлена связь `PVS_SecurityAudit` → `security_admin` |
| `tenants.yml` | Добавлен tenant `security-audit` |

Остальные security-файлы, исходные роли и локальные сервисные пользователи
сохранены. Для них сохранена внутренняя аутентификация, а LDAP-поиск ролей
пропускается через `skip_users`. Bind-учётная запись должна иметь доступ
к чтению OU; имя группы берётся из `cn`, поэтому сопоставление использует
`PVS_SecurityAudit`, а не полный DN. [Механизм LDAP OpenSearch](https://docs.opensearch.org/latest/security/authentication-backends/ldap/).

Словарь переопределяется целиком. Не заменяйте пример только четырьмя изменёнными
ключами: так можно потерять `internal_users.yml`, настройки аудита и другие файлы.
Если на стенде уже есть дополнительные роли, пользователи или mappings,
объедините их с примером до применения. При обновлении версии Kolla повторно
сверяйте полный словарь с новой базовой конфигурацией.

Роль `security_admin` получает:

- чтение индексов `{{ opensearch_log_index_prefix }}-*` — по умолчанию `flog-*`;
- чтение метаданных этих индексов;
- сохранение поисков и dashboards в `security-audit` через `kibana_all_write`.

Запись в tenant относится к сохранённым объектам Dashboards; запись и удаление
событий в `flog-*` не разрешены. `all_access` этой группе не назначается.
Индексы `security-audit-v1-*`, используемые отдельным потоком аудита в архиве,
в новую роль не включены: приложенная модель указывает `flog-*`. Если требуются
оба потока, согласуйте этот набор индексов и расширьте именно `index_patterns`.
CSV-экспорт и доступные плагины Dashboards нужно проверить на ваших образах;
само назначение роли не устанавливает средство экспорта.

В override-файл Dashboards добавьте содержимое
[opensearch_dashboards.yml](examples/opensearch_dashboards.yml).
В исходном шаблоне `opensearch_security.multitenancy.enabled` равен `false`;
без включения этой функции tenant из модели недоступен.
[Документация tenants](https://docs.opensearch.org/latest/security/multi-tenancy/multi-tenancy-config/).

Не создавайте локального пользователя OpenSearch с логином `security_admin`:
в этой схеме его пароль и членство проверяются через AD.

## 4. Применить конфигурацию

Для наследования `virtualization_admin → admin` сначала исправьте настройку
Keystone: по умолчанию `[assignment] prohibited_implied_role = admin` запрещает
создавать такую связь. В `/etc/kolla/config/keystone/keystone.conf` удалите только
`admin` из этого списка, сохранив остальные значения. Если список штатный и содержит
только `admin`, используйте [keystone-rbac.conf](examples/keystone-rbac.conf)
с пустым значением. Это глобальная настройка Keystone: она разрешает использовать
`admin` как implied role вообще, а не только для `virtualization_admin`.
Права на изменение role inferences должны оставаться только у доверенных
администраторов настройки; проверьте эффективную Keystone policy.
[Параметр в Keystone 2025.1](https://docs.openstack.org/keystone/2025.1/configuration/samples/keystone-conf.html).

Применение выполняет оператор в согласованное окно: `reconfigure` может
перезапустить сервисы и повторно загрузить конфигурацию OpenSearch Security.
Команды ниже являются инструкцией; при подготовке документа они не выполнялись.
Замените путь inventory и используйте среду именно этой сборки Kolla.

```bash
kolla-ansible prechecks -i /path/to/inventory --tags keystone,horizon,opensearch --ask-vault-pass
kolla-ansible reconfigure -i /path/to/inventory --tags keystone,horizon,opensearch --ask-vault-pass
```

`globals.d/*.yml` автоматически подхватываются этой версией при каждом запуске.
При следующих deploy/reconfigure/upgrade сохраняйте эти файлы и передавайте
средства расшифровки Ansible Vault. Успешный `prechecks` сам по себе не доказывает
работоспособность LDAP или корректность прав.

Роль OpenSearch вызывает `securityadmin.sh -cd /etc/opensearch/security-audit`.
Поэтому изменения только через UI/API OpenSearch могут быть перезаписаны
следующим применением Kolla. Перед первым применением сохраните актуальную
security-конфигурацию кластера и включите нужные существующие записи в override.

## 5. Создать роли и назначить их группам

Этот раздел — ручной вариант. При установке расширения `pvs-rbac` проектные
назначения и наследование выполняет `post-deploy`; доменное назначение администратора
остаётся отдельной операцией, описанной ниже. Группы Grafana и LCMP здесь не обрабатываются.

Под администратором OpenStack сначала проверьте видимость объектов AD:

```bash
openstack user list --domain AD
openstack group list --domain AD
```

Должны быть видны `virtualization_admin`, `vm_developer` и две соответствующие
группы. Если их нет, сначала исправьте LDAP, CA или поисковые базы.
Создавать дубликаты пользователей и групп командами OpenStack не нужно.

После применения `prohibited_implied_role` из раздела 4 создайте роли
и наследование, если они ещё не настроены:

```bash
openstack role create --or-show virtualization_admin
openstack role create --or-show vm_developer
openstack implied role create --implied-role admin virtualization_admin
openstack implied role create --implied-role member vm_developer
openstack implied role list
```

Убедитесь, что стандартная роль `member` наследует `reader`.
При отсутствии этой связи добавьте `openstack implied role create --implied-role reader member`.

Перед выдачей `vm_developer` подготовьте и проверьте API-политики.
Наследование `member` предоставляет и права удаления, предусмотренные стандартными
политиками. Для запрета удаления ВМ нужно изменить эффективное правило Nova
`os_compute_api:servers:delete`, сохранив доступ предусмотренных моделью администраторов.
Путь override — `/etc/kolla/config/nova/policy.yaml`; поддержка его доставки уже есть.
Готовое выражение правила здесь намеренно не задано: в архиве Kolla нет эффективных
политик и кода Nova из запущенного образа. Их нужно получить из вашей версии,
проверить область токена и все дополнительные назначения, затем применить политику.
Аналогично проверяются остальные ограничения матрицы. До этого этапа группу
разработчиков нельзя считать соответствующей модели.

После подготовки Nova policy и соответствующего override Horizon примените их
через существующий механизм, в согласованное окно:

```bash
kolla-ansible reconfigure -i /path/to/inventory --tags nova,horizon --ask-vault-pass
```

Если меняли политики других API, включите в применение соответствующие сервисы.
Первый вызов reconfigure из раздела 4 с тегами `keystone,horizon,opensearch`
политику Nova не применяет.

Ниже пример назначений после подготовки политик: `PVS` — существующий проект,
`Default` — домен этого проекта, `AD` — домен пользователей и групп.

```bash
openstack role add --group-domain AD --group PVS_VirtAdmins \
  --project-domain Default --project PVS virtualization_admin

openstack role add --group-domain AD --group PVS_VMDevelopers \
  --project-domain Default --project PVS vm_developer
```

Это проектные назначения. Если требуется управление определённым доменом,
назначьте `virtualization_admin` отдельно на согласованный домен через `--domain`.
Права на операции уровня всей платформы зависят от policy и поддерживаемой области
токена конкретного API; одна проектная роль `admin` не доказывает ни доступ ко всем
административным операциям, ни требуемую изоляцию во всех версиях сервисов.
Системные назначения данным примером не выдаются.

## Проверка результата

Администратор проверяет назначения:

```bash
openstack role assignment list --group-domain AD --group PVS_VirtAdmins --names
openstack role assignment list --group-domain AD --group PVS_VMDevelopers --names
```

На узле Nova API проверьте доставку policy без изменения конфигурации.
Пример для Podman; при Docker замените имя команды:

```bash
sudo podman exec nova_api test -r /etc/nova/policy.yaml
sudo podman exec nova_api sh -c 'sed -n "/^\[oslo_policy\]/,/^\[/p" /etc/nova/nova.conf'
sudo podman exec nova_api cat /etc/nova/policy.yaml
```

Для Horizon отдельно проверьте `/etc/openstack-dashboard/nova_policy.yaml`
в контейнере `horizon`. Сверка файлов подтверждает доставку, но разрешение/запрет
конкретной операции подтверждается запросом под соответствующим пользователем.

Каждый пользователь проверяет вход со своими учётными данными и нужным доменом
Keystone. Проверки прав выполняются на тестовых ресурсах и новых сессиях;
ранее выданные токены и кэши членства могут сохранять прежние права.

| Проверка | Ожидаемый результат |
| --- | --- |
| `virtualization_admin`: согласованные административные операции | Доступ в предусмотренных моделью областях; отдельно проверить границы доступа |
| `vm_developer`: создание и изменение тестовой ВМ | Разрешено в назначенном проекте |
| `vm_developer`: удаление тестовой ВМ | Отказ после применения политики Nova |
| `vm_developer`: доступ к ресурсам другого проекта | Отказ при отсутствии дополнительных назначений |
| `security_admin`: вход в OpenSearch, поиск событий `flog-*` | Разрешено |
| `security_admin`: сохранение поиска и dashboard в `security-audit` | Разрешено |
| `security_admin`: экспорт результатов CSV | Проверить через предусмотренный интерфейс экспорта на установленной версии |
| `security_admin`: изменение/удаление событий, индексов и security-конфигурации | Отказ; отрицательные проверки только на выделенных тестовых объектах |

Для проверки полученных групп OpenSearch выполните от `security_admin` запрос
`GET /_plugins/_security/authinfo` и проверьте `backend_roles` и `roles`.
Пример с интерактивным вводом пароля:

```bash
curl --cacert /path/to/opensearch-ca.pem --user security_admin \
  https://opensearch.example.local:9200/_plugins/_security/authinfo
```

Здесь нужен CA HTTPS-сервера OpenSearch; это может быть другой CA, чем у AD.
Ожидаются backend role `PVS_SecurityAudit` и роль `security_admin` без `all_access`.

## Основание инструкции

Проверен архив `kolla-ansible-pvs_1.0.0_14.09.zip`, SHA-256:
`bbb7bfb1398cbe07a12e374cae56f03e9e5c192798cf3ff7842aa75d8c2d7e44`.
Пути ниже относятся к корню распакованного архива:

| Исходник | Подтверждённое поведение |
| --- | --- |
| `kolla_ansible/ansible.py:213` | Чтение дополнительных файлов из `globals.d` |
| `kolla_ansible/ansible.py:267` | Передача этих файлов Ansible при запуске |
| `ansible/roles/keystone/tasks/config.yml:31` | Поиск каталога конфигураций доменов |
| `ansible/roles/keystone/tasks/config.yml:98` | Обработка файлов доменов как шаблонов |
| `ansible/roles/keystone/templates/keystone.conf.j2:30` | Включение отдельных драйверов доменов |
| `ansible/roles/opensearch/defaults/main.yml:239` | Исходный полный набор security-файлов |
| `ansible/roles/opensearch/tasks/config.yml:41` | Доставка security-файлов из словаря |
| `ansible/roles/opensearch/tasks/security-audit-post-config.yml:24` | Применение через securityadmin |
| `ansible/roles/opensearch/templates/opensearch_dashboards.yml.j2:13` | Исходное отключение tenants |
| `ansible/roles/service-cert-copy/tasks/main.yml:14` | Копирование локальных CA; ниже — отдельный путь для Vault |
| `ansible/roles/nova/tasks/config.yml:12` | Поддержка внешнего policy-файла Nova |

Дополнительно: [параметры Keystone](https://docs.openstack.org/keystone/latest/configuration/samples/keystone-conf.html),
[назначение ролей](https://docs.openstack.org/python-openstackclient/ussuri/cli/command-objects/role.html),
[наследование ролей](https://docs.openstack.org/python-openstackclient/zed/cli/command-objects/implied_role.html),
[применение OpenSearch Security](https://docs.opensearch.org/latest/security/configuration/security-admin/).
Upstream-документация поясняет механизм; набор возможностей установленных образов
нужно подтвердить отдельно.
