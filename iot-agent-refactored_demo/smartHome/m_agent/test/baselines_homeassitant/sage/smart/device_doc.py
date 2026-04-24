import json
import os

class Device_info_doc:

    @classmethod
    def get_one_liners_string(cls):
        """
        生成格式为 "{entity_id} ({friendly_name}): {capability}" 的字符串，
        各实体信息以换行符分隔，capability 为 entity_id 中 '.' 前面的部分
        """
        result = []
        from smartHome.m_agent.memory.fake.fake_request import HOMEASSITANT_DATA
        for entity in HOMEASSITANT_DATA.entities:  # 引用类属性entities
            entity_id = entity['entity_id']
            friendly_name = entity['attributes']['friendly_name']
            capability = entity_id.split('.')[0]
            result.append(f"{entity_id} ({friendly_name}): {capability}")

        output = '\n'.join(result)
        return output

    @classmethod
    def get_device_capability_string(cls):
        """
        提取所有实体的capability（entity_id中'.'前面的部分），
        去重后转换为以换行符分隔的字符串
        """
        capabilities = set()
        from smartHome.m_agent.memory.fake.fake_request import HOMEASSITANT_DATA
        for entity in HOMEASSITANT_DATA.entities:  # 引用类属性entities
            entity_id = entity['entity_id']
            capability = entity_id.split('.')[0]
            capabilities.add(capability)

        result = '\n'.join(sorted(capabilities))
        return result  # 返回结果而非打印

if __name__ == "__main__":
    print(Device_info_doc.get_one_liners_string())
    print("======================")
    print(Device_info_doc.get_device_capability_string())