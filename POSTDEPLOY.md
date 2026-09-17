# Автоматизация AD mapping через Kolla post-deploy

Расширение для `kolla-ansible-pvs_1.0.0_14.09.zip`: роли и проектные назначения
Keystone задаются в `globals`, применяются командой `kolla-ansible post-deploy`.
LDAP-подключение предварительно настраивается по [AD-INTEGRATION.md](AD-INTEGRATION.md).

## Что применяется

| Группа | Действие |
| --- | --- |
| `PVS_VirtAdmins` | Создать `virtualization_admin → admin`, назначить группе в выбранном проекте |
| `PVS_VMDevelopers` | Создать `vm_developer → member → reader`, назначить группе в выбранном проекте после проверки policy |
| `PVS_SecurityAudit` | Подставить в OpenSearch `roles_mapping.yml`; файл применяет штатный `deploy/reconfigure` |
| `PVS_GrafanaAdmins` | Создаётся AD-скриптом; LDAP mapping Grafana настраивается отдельно |

Назначения **на домен и system scope не создаются**. Доменное назначение
`virtualization_admin`, предусмотренное моделью, выполняется отдельно по
[разделу 5 инструкции](AD-INTEGRATION.md#5-создать-роли-и-назначить-их-группам).
Администратор проекта не становится автоматически администратором всех доменов.
При read-only LDAP создание пользователей AD остаётся операцией AD, даже если
пользователю назначена административная роль Keystone.

## 1. Установить расширение

В репозитории поставляются только:

- [роль pvs-rbac](kolla/ansible/roles/pvs-rbac/tasks/main.yml), её defaults и локальный модуль;
- [patch post-deploy](kolla/post-deploy.patch), добавляющий один необязательный play;
- [пример globals](examples/95-pvs-rbac.yml).

Архив проекта не изменён. Patch подготовлен для его `ansible/post-deploy.yml`.
На deploy-узле активируйте Python-окружение именно вашей сборки Kolla и перейдите
в скачанный репозиторий AD. Установите SDK в это же окружение:

```bash
python -m pip install -r kolla/requirements.txt
```

Роль использует один небольшой локальный модуль с `openstacksdk`: он сначала
проверяет все объекты и планирует изменения, затем выполняет их. Коллекция
`openstack.cloud` для этого расширения не требуется. Используются штатные API
[назначения ролей SDK](https://docs.openstack.org/openstacksdk/latest/user/proxies/identity_v3.html#openstack.identity.v3._proxy.Proxy.assign_project_role_to_group)
и [Keystone role inferences](https://docs.openstack.org/api-ref/identity/v3/#role-inferences).

Определите каталог playbook-файлов установленной Kolla:

```bash
PVS_KOLLA_ANSIBLE_DIR="$(python -c 'from kolla_ansible import utils; print(utils.get_data_files_path("ansible"))')"
printf '%s\n' "$PVS_KOLLA_ANSIBLE_DIR"
patch --dry-run -p2 -d "$PVS_KOLLA_ANSIBLE_DIR" < kolla/post-deploy.patch
```

Перед первым применением сохраните свою копию `post-deploy.yml`; если роль
`pvs-rbac` уже установлена и изменялась, сначала сравните её с новой версией.
После успешного `--dry-run`:

```bash
cp -p "$PVS_KOLLA_ANSIBLE_DIR/post-deploy.yml" "$PVS_KOLLA_ANSIBLE_DIR/post-deploy.yml.before-pvs-rbac"
cp -R kolla/ansible/roles/pvs-rbac "$PVS_KOLLA_ANSIBLE_DIR/roles/"
patch -p2 -d "$PVS_KOLLA_ANSIBLE_DIR" < kolla/post-deploy.patch
```

Повторно patch накладывать не нужно. После обновления пакета Kolla проверяйте,
что hook сохранился; при изменении исходного playbook адаптируйте patch.
Расширение не запускает `post-deploy` автоматически после `deploy`: вызывайте
обе команды в нужном порядке в своём deployment pipeline.

## 2. Задать группы и проект

Разместите [95-pvs-rbac.yml](examples/95-pvs-rbac.yml) в `/etc/kolla/globals.d/`.
Эта версия Kolla читает `globals.d` по порядку имён при каждом запуске.
Секреты в этот файл не добавляются; используется созданный Kolla `clouds.yaml`.

```yaml
pvs_rbac_enabled: true
pvs_ad_domain: AD
pvs_ad_groups:
  virtualization_admin: PVS_VirtAdmins
  vm_developer: PVS_VMDevelopers
  security_admin: PVS_SecurityAudit
pvs_keystone_project: PVS
pvs_keystone_project_domain: Default
pvs_admin_implied_role_ready: false
pvs_vm_developer_policy_ready: false
```

`pvs_ad_domain` — имя домена Keystone, обслуживаемого LDAP, а не DNS-домен AD.
`pvs_keystone_project_domain` — домен существующего проекта; он может быть другим.
Группы, домены и проект должны существовать и однозначно находиться по именам.
Пользователи, группы AD и проект автоматически не создаются.

В обновлённых примерах [90-pvs-ad.yml](examples/90-pvs-ad.yml) и
[keystone.AD.conf](examples/keystone.AD.conf) имена трёх групп берутся из
`pvs_ad_groups`. Словарь в `95-pvs-rbac.yml` имеет приоритет над таким же словарём
в `90-pvs-ad.yml`; указывайте все три ключа. Пример LDAP предполагает группы
непосредственно в `pvs_ad_base_dn` и простые CN как в AD-скрипте. Для других DN
или специальных символов нужно адаптировать и экранировать LDAP-фильтры.

Если задан нестандартный `--configdir`, используйте его и при `post-deploy`:
путь к `clouds.yaml` берётся из `node_config`. По умолчанию используется cloud
`kolla-admin`. При необходимости задайте `pvs_rbac_cloud: kolla-admin-system`
или другой административный cloud, разрешённый политиками вашей версии Keystone.
Это определяет credentials выполняющего операцию администратора, а не scope
выдаваемых группе прав. CA и проверка TLS берутся из cloud; проверка не отключается.
Новая localhost-задача явно использует `ansible_playbook_python`, то есть Python
запущенного Ansible. Это исключает случайный выбор системного Python без SDK
из-за явного `localhost` в inventory. Для другого окружения задайте
`pvs_rbac_python_interpreter` и установите SDK туда. Не задавайте конфликтующий
глобальный `-e ansible_python_interpreter=...`: extra-vars имеют более высокий
приоритет. Пути CA должны быть доступны deploy-узлу.

## 3. Применить LDAP, OpenSearch и policies

Штатный Keystone запрещает наследование `admin` через
`[assignment] prohibited_implied_role = admin`. Для согласованной модели
`virtualization_admin → admin` объедините [keystone-rbac.conf](examples/keystone-rbac.conf)
с `/etc/kolla/config/keystone/keystone.conf`: удалите из существующего списка только
`admin`, сохранив остальные запреты. Пустое значение подходит только для штатного
списка из одного `admin`. Это глобальное разрешение наследовать `admin`;
оно не ограничено выбранной группой или проектом. Проверьте, кому эффективные
Keystone policies позволяют создавать/удалять role inferences.
[Штатный параметр Keystone 2025.1](https://docs.openstack.org/keystone/2025.1/configuration/samples/keystone-conf.html).

После применения и проверки конфигурации Keystone установите:

```yaml
pvs_admin_implied_role_ready: true
```

Без этого подтверждения запрос `virtualization_admin: present` останавливает модуль
до любых записей. Флаг не читает конфигурацию сервера: если ошибочно выставить `true`
при сохранённом запрете, Keystone отклонит создание связи, а предшествующие успешные
операции не откатятся. Для `absent` это подтверждение не требуется.

Примените LDAP и OpenSearch overrides штатным `reconfigure`, как описано в
[инструкции](AD-INTEGRATION.md#4-применить-конфигурацию). OpenSearch читает группу
`pvs_ad_groups.security_admin` как backend role при LDAP `rolename: cn`.
Существующий полный словарь `opensearch_security_audit_config` и остальные mappings
нужно сохранить. API-вызовы OpenSearch из `post-deploy` не выполняются.

До выдачи `vm_developer` примените API policies и проверьте все запреты матрицы,
в том числе удаление, запуск/остановку и создание снимков ВМ. Затем установите:

```yaml
pvs_vm_developer_policy_ready: true
```

Это подтверждение оператора, **не автоматическая проверка policy**. При `false`
и запрошенном `vm_developer: present` модуль завершится ошибкой до любых изменений
Keystone. Для настройки только администратора на первом этапе укажите:

```yaml
pvs_keystone_assignments:
  - role: virtualization_admin
    state: present
```

Понадобится согласованное окно применения: `reconfigure` может перезапускать сервисы.
Пакет не содержит готовой полной policy для вашей версии сервисов.

## 4. Запустить post-deploy

```bash
kolla-ansible post-deploy -i /path/to/inventory --ask-vault-pass
```

`--ask-vault-pass` нужен, если переменные зашифрованы Ansible Vault. При использовании
SecMan сохраняется исходный механизм Kolla: `post-deploy` получает административный
пароль штатным bootstrap и формирует клиентские файлы с правами `0600`.

Для предварительного просмотра можно использовать `--check`. На первом запуске
сначала сформируйте клиентские файлы без RBAC, поскольку check mode не записывает
новый `clouds.yaml`:

```bash
kolla-ansible post-deploy -i /path/to/inventory --ask-vault-pass -e pvs_rbac_enabled=false
kolla-ansible post-deploy -i /path/to/inventory --ask-vault-pass --check
```

Если менялись адреса, CA или административные credentials, сначала обновите
клиентские файлы тем же способом. В check mode RBAC читает Keystone и возвращает
план `operations`, без создания ролей, связей и назначений.

## Повторное применение и отзыв

Второй запуск с тем же состоянием не меняет назначения. Модуль останавливается
при отсутствующей/неоднозначной группе, отключённом домене/проекте, отсутствии
стандартной роли или лишних связях наследования у управляемой пользовательской роли.
Наследование ролей глобально: новая связь применяется ко всем существующим
назначениям этих ролей, а не только к выбранной группе.

Для отзыва конкретного проектного назначения сохраните его с `absent`:

```yaml
pvs_keystone_assignments:
  - role: virtualization_admin
    state: present
  - role: vm_developer
    state: absent
```

Удаление строки из списка или `pvs_rbac_enabled: false` **не отзывает права**.
При смене группы/проекта сначала отзовите старое назначение со старыми параметрами,
затем примените новые. `absent` не удаляет сами роли и глобальные связи наследования.
Проверка готовности developer policy не блокирует отзыв.

Остальные прямые, групповые и наследуемые назначения не изменяются. Поэтому отзыв
одного назначения не гарантирует отсутствие другого пути доступа. Проверьте итоговые
права на новой сессии с учётом кэшей LDAP и ранее выданных токенов.
При ошибке посреди записи уже успешные операции сохраняются; транзакционного
отката нет. После исправления причины повторный запуск завершает оставшуюся работу.

## Проверка реализации

```bash
python3 -m unittest discover -s tests -v
pwsh -NoProfile -File tests/Test-AdAccounts.ps1
```

Первый запуск без SDK выполняет 22 теста логики и пропускает HTTP-интеграционный тест.
Чтобы выполнить его, установите SDK и Ansible в тестовое окружение и запустите:

```bash
PVS_ANSIBLE_PLAYBOOK="$(command -v ansible-playbook)" python3 -m unittest discover -s tests -v
```

Интеграционный тест запускает Ansible и настоящий SDK против поддельного Keystone
на `127.0.0.1`: disabled, policy gate, check, создание, повторный запуск и отзыв.
Он не использует credentials реального облака. При подготовке пакета проверены
Ansible Core 2.18.2, SDK 4.4.0, PowerShell 7.4.6 с подставными AD-командами,
структура YAML и применимость patch к исходному архиву.
Работа с настоящими AD, Keystone, OpenSearch, Grafana и LCMP не проверялась.
